"""
Module for generating atmospheric model spectra with ``petitRADTRANS``. Details on this
atmospheric retrieval code can be found in Mollière et al. (2019) and in the online
documentation (https://petitradtrans.readthedocs.io).
"""

from typing import Dict, Optional, List, Tuple

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np

from typeguard import typechecked

from species.analysis import photometry
from species.core import box, constants
from species.read import read_filter
from species.util import dust_util, read_util, retrieval_util


class ReadRadtrans:
    """
    Class for generating a model spectrum with ``petitRADTRANS``.
    """

    @typechecked
    def __init__(self,
                 line_species: Optional[List[str]] = None,
                 cloud_species: Optional[List[str]] = None,
                 scattering: bool = False,
                 wavel_range: Optional[Tuple[float, float]] = None,
                 filter_name: Optional[str] = None,
                 pressure_grid: str = 'smaller') -> None:
        """
        Parameters
        ----------
        line_species : list, None
            List with the line species. No line species are used if set to ``None``.
        cloud_species : list, None
            List with the cloud species. No clouds are used if set to ``None``.
        scattering : bool
            Include scattering in the radiative transfer.
        wavel_range : tuple(float, float), None
            Wavelength range (um). The wavelength range is set to 0.8-10 um if set to ``None`` or
            not used if ``filter_name`` is not ``None``.
        filter_name : str, None
            Filter name that is used for the wavelength range. The ``wavel_range`` is used if
            ''filter_name`` is set to ``None``.
        pressure_grid : str
            The type of pressure grid that is used for the radiative transfer. Either 'standard',
            to use 180 layers both for the atmospheric structure (e.g. when interpolating the
            abundances) and 180 layers with the radiative transfer, or 'smaller' to use 60 (instead
            of 180) with the radiative transfer, or 'clouds' to start with 1440 layers but resample
            to ~100 layers (depending on the number of cloud species) with a refinement around the
            cloud decks. For cloudless atmospheres it is recommended to use 'smaller', which runs
            faster than 'standard' and provides sufficient accuracy. For cloudy atmosphere, one can
            test with 'smaller' but it is recommended to use 'clouds' for improved accuracy fluxes.

        Returns
        -------
        NoneType
            None
        """

        self.filter_name = filter_name
        self.wavel_range = wavel_range
        self.scattering = scattering
        self.pressure_grid = pressure_grid

        if self.filter_name is not None:
            transmission = read_filter.ReadFilter(self.filter_name)
            self.wavel_range = transmission.wavelength_range()
            self.wavel_range = (0.9*self.wavel_range[0], 1.2*self.wavel_range[1])

        elif self.wavel_range is None:
            self.wavel_range = (0.8, 10.)

        if line_species is None:
            self.line_species = []
        else:
            self.line_species = line_species

        if cloud_species is None:
            self.cloud_species = []
            n_pressure = 180

        else:
            self.cloud_species = cloud_species
            # n_pressure = 1440
            n_pressure = 180

        # Create 180 pressure layers in log space
        self.pressure = np.logspace(-6, 3, n_pressure)

        # Import petitRADTRANS here because it is slow

        print('Importing petitRADTRANS...', end='', flush=True)
        from petitRADTRANS.radtrans import Radtrans
        print(' [DONE]')

        # Create the Radtrans object

        self.rt_object = Radtrans(line_species=self.line_species,
                                  rayleigh_species=['H2', 'He'],
                                  cloud_species=self.cloud_species,
                                  continuum_opacities=['H2-H2', 'H2-He'],
                                  wlen_bords_micron=self.wavel_range,
                                  mode='c-k',
                                  test_ck_shuffle_comp=self.scattering,
                                  do_scat_emis=self.scattering)

        # Create the RT arrays

        if self.pressure_grid == 'standard':
            self.rt_object.setup_opa_structure(self.pressure)

        elif self.pressure_grid == 'smaller':
            self.rt_object.setup_opa_structure(self.pressure[::3])

        elif self.pressure_grid == 'clouds':
            self.rt_object.setup_opa_structure(self.pressure[::24])

    @typechecked
    def get_model(self,
                  model_param: Dict[str, float],
                  spec_res: Optional[float] = None,
                  wavel_resample: Optional[np.ndarray] = None,
                  plot_contribution: Optional[str] = None) -> box.ModelBox:
        """
        Function for calculating a model spectrum.

        Parameters
        ----------
        model_param : dict
            Dictionary with the model parameters and values.
        spec_res : float, None
            Spectral resolution, achieved by smoothing with a Gaussian kernel. No smoothing is
            applied when set to ``None``.
        wavel_resample : np.ndarray, None
            Wavelength points (um) to which the spectrum is resampled. The original wavelengths
            points are used if set to ``None``.
        plot_contribution : str, None
            Filename for the plot with the emission contribution. The plot is not created if set
            to ``None``.

        Returns
        -------
        species.core.box.ModelBox
            Box with the model spectrum.
        """

        if spec_res is not None and wavel_resample is not None:
            raise ValueError('The \'spec_res\' and \'wavel_resample\' parameters can not be used '
                             'simultaneously. Please set one of them to None.')

        if plot_contribution:
            contribution = True
        else:
            contribution = False

        if 'tint' in model_param:
            temp, _ = retrieval_util.pt_ret_model(
                np.array([model_param['t1'], model_param['t2'], model_param['t3']]),
                10.**model_param['log_delta'], model_param['alpha'], model_param['tint'],
                self.pressure, model_param['metallicity'], model_param['c_o_ratio'])

        else:
            knot_press = np.logspace(np.log10(self.pressure[0]), np.log10(self.pressure[-1]), 15)

            knot_temp = []
            for i in range(15):
                knot_temp.append(model_param[f't{i}'])

            knot_temp = np.asarray(knot_temp)

            temp = retrieval_util.pt_spline_interp(knot_press, knot_temp, self.pressure)

        if 'log_p_quench' in model_param:
            log_p_quench = model_param['log_p_quench']
        else:
            log_p_quench = -10.

        if len(self.cloud_species) > 0:
            cloud_fractions = {}

            for item in self.cloud_species:
                cloud_param = f'{item[:-3].lower()}_fraction'

                if cloud_param in model_param:
                    cloud_fractions[item] = model_param[cloud_param]

                cloud_param = f'{item[:-3].lower()}_tau'

                if cloud_param in model_param:
                    if 'log_p_quench' in model_param:
                        # Quenching pressure (bar)
                        quench_pressure = 10.**model_param['log_p_quench']
                    else:
                        quench_pressure = None

                    # Import the chemistry module here because it is slow
                    from poor_mans_nonequ_chem_FeH.poor_mans_nonequ_chem.poor_mans_nonequ_chem \
                        import interpol_abundances

                    # Interpolate the abundances, following chemical equilibrium
                    abund_in = interpol_abundances(np.full(self.pressure.size,
                                                           model_param['c_o_ratio']),
                                                   np.full(self.pressure.size,
                                                           model_param['metallicity']),
                                                   temp,
                                                   self.pressure,
                                                   Pquench_carbon=quench_pressure)

                    # Extract the mean molecular weight
                    mmw = abund_in['MMW']

                    # Calculate the scaled mass fraction of the clouds
                    cloud_fractions[item] = retrieval_util.scale_cloud_abund(
                        model_param, self.rt_object, self.pressure, temp, mmw,
                        'equilibrium', abund_in, item, model_param[cloud_param],
                        pressure_grid=self.pressure_grid)

            log_x_base = retrieval_util.log_x_cloud_base(model_param['c_o_ratio'],
                                                         model_param['metallicity'],
                                                         cloud_fractions)

            wavelength, flux, emission_contr = retrieval_util.calc_spectrum_clouds(
                self.rt_object, self.pressure, temp, model_param['c_o_ratio'],
                model_param['metallicity'], log_p_quench, log_x_base, model_param['fsed'],
                model_param['kzz'], model_param['logg'], model_param['sigma_lnorm'],
                chemistry='equilibrium', pressure_grid=self.pressure_grid,
                plotting=False, contribution=contribution)

        elif 'c_o_ratio' in model_param and 'metallicity' in model_param:
            wavelength, flux, emission_contr = retrieval_util.calc_spectrum_clear(
                self.rt_object, self.pressure, temp, model_param['logg'],
                model_param['c_o_ratio'], model_param['metallicity'], log_p_quench,
                None, pressure_grid=self.pressure_grid, chemistry='equilibrium',
                contribution=contribution)

        else:
            abund = {}
            for ab_item in self.rt_object.line_species:
                abund[ab_item] = model_param[ab_item]

            wavelength, flux, emission_contr = retrieval_util.calc_spectrum_clear(
                self.rt_object, self.pressure, temp, model_param['logg'], None,
                None, None, abund, pressure_grid=self.pressure_grid,
                chemistry='free', contribution=contribution)

        if 'radius' in model_param:
            model_param['mass'] = read_util.get_mass(model_param['logg'], model_param['radius'])

            if 'distance' in model_param:
                scaling = (model_param['radius']*constants.R_JUP)**2 / \
                          (model_param['distance']*constants.PARSEC)**2

                flux *= scaling

        if 'ism_ext' in model_param:
            if 'ism_red' in model_param:
                ism_reddening = model_param['ism_red']

            else:
                # Use default ISM reddening (R_V = 3.1) if ism_red is not provided
                ism_reddening = 3.1

            flux = dust_util.apply_ism_ext(wavelength,
                                           flux,
                                           model_param['ism_ext'],
                                           ism_reddening)

        if spec_res is not None:
            # convolve with Gaussian LSF
            flux = retrieval_util.convolve(wavelength, flux, spec_res)

        if plot_contribution is not None:
            mpl.rcParams['font.serif'] = ['Bitstream Vera Serif']
            mpl.rcParams['font.family'] = 'serif'

            plt.rc('axes', edgecolor='black', linewidth=2.5)

            plt.figure(1, figsize=(8., 4.))
            gridsp = mpl.gridspec.GridSpec(1, 1)
            gridsp.update(wspace=0, hspace=0, left=0, right=1, bottom=0, top=1)

            ax = plt.subplot(gridsp[0, 0])

            ax.tick_params(axis='both', which='major', colors='black', labelcolor='black',
                           direction='in', width=1, length=5, labelsize=12, top=True,
                           bottom=True, left=True, right=True)

            ax.tick_params(axis='both', which='minor', colors='black', labelcolor='black',
                           direction='in', width=1, length=3, labelsize=12, top=True,
                           bottom=True, left=True, right=True)

            ax.set_xlabel(r'Wavelength ($\mu$m)', fontsize=13)
            ax.set_ylabel('Pressure (bar)', fontsize=13)

            ax.get_xaxis().set_label_coords(0.5, -0.09)
            ax.get_yaxis().set_label_coords(-0.07, 0.5)

            ax.set_yscale('log')
            ax.set_xscale('log')

            xx_grid, yy_grid = np.meshgrid(wavelength, self.pressure[::3])
            ax.contourf(xx_grid, yy_grid, emission_contr, 30, cmap=plt.cm.bone_r)

            ax.set_xlim(np.amin(wavelength), np.amax(wavelength))
            ax.set_ylim(np.amax(self.pressure[::3]), np.amin(self.pressure[::3]))

            plt.savefig(plot_contribution, bbox_inches='tight')
            plt.clf()
            plt.close()

        return box.create_box(boxtype='model',
                              model='petitradtrans',
                              wavelength=wavelength,
                              flux=flux,
                              parameters=model_param,
                              quantity='flux')

    @typechecked
    def get_flux(self,
                 model_param: Dict[str, float]) -> Tuple[float, None]:
        """
        Function for calculating the average flux density for the ``filter_name``.

        Parameters
        ----------
        model_param : dict
            Dictionary with the model parameters and values.

        Returns
        -------
        float
            Flux (W m-2 um-1).
        NoneType
            Error (W m-2 um-1). Always set to ``None``.
        """

        spectrum = self.get_model(model_param)

        synphot = photometry.SyntheticPhotometry(self.filter_name)

        return synphot.spectrum_to_flux(spectrum.wavelength, spectrum.flux)
