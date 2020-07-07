"""
Module with functionalities for atmospheric retrieval with petitRADTRANS (Mollière et al. 2019).
More details on the retrieval code are available at https://petitradtrans.readthedocs.io.
"""

import os
import json
import warnings

from typing import Optional

import pymultinest
import numpy as np
import matplotlib.pyplot as plt

from typeguard import typechecked
from scipy.stats import invgamma
from rebin_give_width import rebin_give_width

from petitRADTRANS import Radtrans
from petitRADTRANS_ck_test_speed import Radtrans as RadtransScatter

from species.analysis import photometry
from species.data import database
from species.core import constants
from species.read import read_object
from species.util import retrieval_util


os.environ['OMP_NUM_THREADS'] = '1'


class AtmosphericRetrieval:
    """
    Class for atmospheric retrieval with petitRADTRANS.
    """

    @typechecked
    def __init__(self,
                 object_name: str,
                 line_species: Optional[list],
                 cloud_species: Optional[list],
                 scattering: bool,
                 output_folder: str) -> None:
        """
        Parameters
        ----------
        object_name : str
            Object name in the database.
        line_species : list, None
            List with the line species. No line species are used if set to None.
        cloud_species : list, None
            List with the cloud species. No cloud species are used if set to None.
        scattering : bool
            Include scattering in the radiative transfer.
        output_folder : str
            Folder name that is used for the output files from MultiNest.

        Returns
        -------
        NoneType
            None
        """

        # input parameters

        self.object_name = object_name
        self.line_species = line_species
        self.cloud_species = cloud_species
        self.scattering = scattering
        self.output_folder = output_folder

        # get object data

        self.object = read_object.ReadObject(self.object_name)
        self.distance = self.object.get_distance()[0]  # [pc]

        print(f'Object: {self.object_name}')
        print(f'Distance: {self.distance}')

        if self.line_species is None:
            print(f'Line species: None')
            self.line_species = []

        else:
            print(f'Line species:')
            for item in self.line_species:
                print(f'   - {item}')

        if self.cloud_species is None:
            print(f'Cloud species: None')
            self.cloud_species = []

        else:
            print(f'Cloud species:')
            for item in self.cloud_species:
                print(f'   - {item}')

        print(f'Scattering: {self.scattering}')

        species_db = database.Database()

        objectbox = species_db.get_object(object_name,
                                          inc_phot=True,
                                          inc_spec=True)

        # get photometric data

        self.objphot = []
        self.synphot = []

        if len(objectbox.filters) != 0:
            warnings.warn('Support for photometric data is not yet implemented.')
            print('Photometric data:')

        for item in objectbox.filters:
            obj_phot = self.object.get_photometry(item)
            self.objphot.append((obj_phot[2], obj_phot[3]))

            print(f'   - {item} (W m-2 um-1) = {obj_phot[2]:.2e} +/- {obj_phot[3]}')

            sphot = photometry.SyntheticPhotometry(item)
            self.synphot.append(sphot)

        if not self.objphot:
            self.objphot = None

        if not self.synphot:
            self.synphot = None

        # get spectroscopic data

        self.spectrum = self.object.get_spectrum()

        if self.spectrum is None:
            raise ValueError('A spectrum is required for atmospheric retrieval.')

        # set wavelength bins and add to spectrum dictionary

        self.wavel_min = []
        self.wavel_max = []

        print('Spectroscopic data:')

        for key, value in self.spectrum.items():
            dict_val = list(value)
            wavel_data = dict_val[0][:, 0]

            wavel_bins = np.zeros_like(wavel_data)
            wavel_bins[:-1] = np.diff(wavel_data)
            wavel_bins[-1] = wavel_bins[-2]

            dict_val.append(wavel_bins)
            self.spectrum[key] = dict_val

            # min and max wavelength for Radtrans object

            self.wavel_min.append(wavel_data[0])
            self.wavel_max.append(wavel_data[-1])

            print(f'   - {key}')
            print(f'     Wavelength range (um) = {wavel_data[0]:.2f} - {wavel_data[-1]:.2f}')
            print(f'     Spectral resolution = {self.spectrum[key][3]:.2f}')

        # create the pressure layers for the Radtrans object

        if len(self.cloud_species) > 0:
            # initiate many pressure layers for the refinement around the cloud decks
            n_pressure = 1440

        else:
            # initiate fewer pressure layers for a cloudless atmosphere
            n_pressure = 180

        self.pressure = np.logspace(-6, 3, n_pressure)

        print(f'Initiating {self.pressure.size} pressure levels (bar): '
              f'{self.pressure[0]:.2e} - {self.pressure[-1]:.2e}')

        # initiate parameter list and counters

        self.parameters = []

    @typechecked
    def set_parameters(self,
                       bounds: dict,
                       chemistry: str,
                       quenching: bool,
                       pt_profile: str) -> None:
        """
        Function to set the list with parameters.

        Parameters
        ----------
        bounds : dict
            Dictionary with the parameter boundaries.
        chemistry : str
            The chemistry type: 'equilibrium' for equilibrium chemistry or 'free' for retrieval
            of free abundances (but constant with altitude).
        quenching : bool
            Fitting a quenching pressure.
        pt_profile : str
            The parametrization for the pressure-temperature profile ('molliere', 'free', or
            'monotonic').

        Returns
        -------
        NoneType
            None
        """

        # check if clouds are used in combination with equilibrium chemistry

        if len(self.cloud_species) and chemistry != 'equilibrium':
            raise ValueError('Clouds are currently only implemented in combination with '
                             'equilibrium chemistry.')

        # generic parameters

        self.parameters.append('logg')
        self.parameters.append('radius')

        # p-t profile parameters

        if pt_profile == 'molliere':
            self.parameters.append('tint')
            self.parameters.append('t1')
            self.parameters.append('t2')
            self.parameters.append('t3')
            self.parameters.append('alpha')
            self.parameters.append('log_delta')

        elif pt_profile in ['free', 'monotonic']:
            for i in range(15):
                self.parameters.append(f't{i}')

            if pt_profile == 'free':
                self.parameters.append('gamma_r')
                self.parameters.append('beta_r')

        # abundance parameters

        if chemistry == 'equilibrium':

            self.parameters.append('metallicity')
            self.parameters.append('c_o_ratio')

        elif chemistry == 'free':

            for item in self.line_species:
                self.parameters.append(item)

        if quenching:
            self.parameters.append('log_p_quench')

        # cloud parameters

        if len(self.cloud_species) > 0:
            if 'Fe(c)_cd' in self.cloud_species:
                self.parameters.append('fe_fraction')

            if 'MgSiO3(c)_cd' in self.cloud_species:
                self.parameters.append('mgsio3_fraction')

            if 'Na2S(c)_cd' in self.cloud_species:
                self.parameters.append('na2s_fraction')

            if 'KCL(c)_cd' in self.cloud_species:
                self.parameters.append('kcl_fraction')

            self.parameters.append('fsed')
            self.parameters.append('kzz')
            self.parameters.append('sigma_lnorm')

        # add the flux scaling parameters

        for item in self.spectrum:
            if item in bounds:
                if bounds[item][0] is not None:
                    self.parameters.append(f'scaling_{item}')

        # add the error offset parameters

        for item in self.spectrum:
            if item in bounds:
                if bounds[item][1] is not None:
                    self.parameters.append(f'error_{item}')

        # add the wavelength calibration parameters

        for item in self.spectrum:
            if item in bounds:
                if bounds[item][2] is not None:
                    self.parameters.append(f'wavelength_{item}')

        print(f'Fitting {len(self.parameters)} parameters:')

        for item in self.parameters:
            print(f'   - {item}')

    @typechecked
    def run_multinest(self,
                      bounds: dict,
                      chemistry: str = 'equilibrium',
                      quenching: bool = True,
                      pt_profile: str = 'molliere',
                      live_points: int = 2000,
                      resume: bool = False,
                      plotting: bool = False) -> None:
        """
        Function to run the ``PyMultiNest`` wrapper of the ``MultiNest`` sampler. While
        ``PyMultiNest`` can be installed with ``pip`` from the PyPI repository, ``MultiNest``
        has to to be build manually. See the ``PyMultiNest`` documentation for details:
        http://johannesbuchner.github.io/PyMultiNest/install.html. Note that the library path
        of ``MultiNest`` should be set to the environmental variable ``LD_LIBRARY_PATH`` on a
        Linux machine and ``DYLD_LIBRARY_PATH`` on a Mac. Alternatively, the variable can be
        set before importing the ``species`` package, for example:

        .. code-block:: python

            >>> import os
            >>> os.environ['DYLD_LIBRARY_PATH'] = '/path/to/MultiNest/lib'
            >>> import species

        Parameters
        ----------
        bounds : dict
            Dictionary with the prior boundaries.
        chemistry : str
            The chemistry type: 'equilibrium' for equilibrium chemistry or 'free' for retrieval
            of free abundances (but constant with altitude).
        quenching : bool
            Fitting a quenching pressure.
        pt_profile : str
            The parametrization for the pressure-temperature profile ('molliere', 'free', or
            'monotonic').
        live_points : int
            Number of live points.
        resume : bool
            Resume from a previous run.
        plotting : bool
            Plot sample results for testing.

        Returns
        -------
        NoneType
            None
        """

        # create the output folder if required

        if not os.path.exists(self.output_folder):
            os.mkdir(self.output_folder)

        # create list with parameters for MultiNest

        if quenching and chemistry != 'equilibrium':
            raise ValueError('The \'quenching\' parameter can only be used in combination with '
                             'chemistry=\'equilibrium\'.')

        self.set_parameters(bounds, chemistry, quenching, pt_profile)

        # create a dictionary with the cube indices of the parameters

        cube_index = {}
        for i, item in enumerate(self.parameters):
            cube_index[item] = i

        # delete the cloud parameters from the boundaries dictionary in case of no cloud species

        if len(self.cloud_species) == 0:
            if 'fe_fraction' in bounds:
                del bounds['fe_fraction']

            if 'mgsio3_fraction' in bounds:
                del bounds['mgsio3_fraction']

            if 'na2s_fraction' in bounds:
                del bounds['na2s_fraction']

            if 'kcl_fraction' in bounds:
                del bounds['kcl_fraction']

            if 'fsed' in bounds:
                del bounds['fsed']

            if 'kzz' in bounds:
                del bounds['kzz']

            if 'sigma_lnorm' in bounds:
                del bounds['sigma_lnorm']

        # delete C/H and O/H boundaries if the chemistry is not free

        if chemistry != 'free':
            if 'c_h_ratio' in bounds:
                del bounds['c_h_ratio']

            if 'o_h_ratio' in bounds:
                del bounds['o_h_ratio']

        # create Ratrans object

        print('Setting up petitRADTRANS...')

        if self.scattering:
            # the names in self.cloud_species are converted
            rt_object = RadtransScatter(line_species=self.line_species,
                                        rayleigh_species=['H2', 'He'],
                                        cloud_species=self.cloud_species,
                                        continuum_opacities=['H2-H2', 'H2-He'],
                                        wlen_bords_micron=(0.95*min(self.wavel_min),
                                                           1.15*max(self.wavel_max)),
                                        mode='c-k',
                                        test_ck_shuffle_comp=self.scattering,
                                        do_scat_emis=self.scattering)

        else:
            rt_object = Radtrans(line_species=self.line_species,
                                 rayleigh_species=['H2', 'He'],
                                 cloud_species=self.cloud_species,
                                 continuum_opacities=['H2-H2', 'H2-He'],
                                 wlen_bords_micron=(0.95*min(self.wavel_min),
                                                    1.15*max(self.wavel_max)),
                                 mode='c-k')

        # create RT arrays of 60 pressure layers

        if len(self.cloud_species) > 0:
            rt_object.setup_opa_structure(self.pressure[::24])

        else:
            rt_object.setup_opa_structure(self.pressure[::3])

            print(f'Decreasing the number of pressure levels: {self.pressure.size} -> '
                  f'{self.pressure[::3].size}.')

        if pt_profile in ['free', 'monotonic']:
            knot_press = np.logspace(np.log10(self.pressure[0]), np.log10(self.pressure[-1]), 15)

        @typechecked
        def prior(cube,
                  n_dim: int,
                  n_param: int) -> None:
            """
            Function to transform the unit cube into the parameter cube.

            Parameters
            ----------
            cube : LP_c_double
                Unit cube.
            n_dim : int
                Number of dimensions.
            n_param : int
                Number of parameters.

            Returns
            -------
            NoneType
                None
            """

            # surface gravity (dex)
            if 'logg' in bounds:
                logg = bounds['logg'][0] + (bounds['logg'][1]-bounds['logg'][0])*cube[cube_index['logg']]
            else:
                # default: 2 - 5.5 dex
                logg = 2. + 3.5*cube[cube_index['logg']]

            cube[cube_index['logg']] = logg

            # planet radius (Rjup)
            if 'radius' in bounds:
                radius = bounds['radius'][0] + (bounds['radius'][1]-bounds['radius'][0])*cube[cube_index['radius']]
            else:
                # defaul: 0.8-2 Rjup
                radius = 0.8 + 1.2*cube[cube_index['radius']]

            cube[cube_index['radius']] = radius

            if pt_profile == 'molliere':

                # internal temperature (K) of the Eddington approximation (middle altitudes)
                # see Eq. 2 in Mollière et al. (2020)
                if 'tint' in bounds:
                    tint = bounds['tint'][0] + (bounds['tint'][1]-bounds['tint'][0])*cube[cube_index['tint']]
                else:
                    # default: 500 - 3000 K
                    tint = 500. + 2500.*cube[cube_index['tint']]

                cube[cube_index['tint']] = tint

                # connection temperature (K)
                t_connect = (3./4.*tint**4.*(0.1+2./3.))**0.25

                # the temperature (K) at temp_3 is scaled down from t_connect
                temp_3 = t_connect*(1-cube[cube_index['t3']])
                cube[cube_index['t3']] = temp_3

                # the temperature (K) at temp_2 is scaled down from temp_3
                temp_2 = temp_3*(1-cube[cube_index['t2']])
                cube[cube_index['t2']] = temp_2

                # the temperature (K) at temp_1 is scaled down from temp_2
                temp_1 = temp_2*(1-cube[cube_index['t1']])
                cube[cube_index['t1']] = temp_1

                # alpha: power law index in tau = delta * press_cgs**alpha
                # see Eq. 1 in Mollière et al. (2020)
                if 'alpha' in bounds:
                    alpha = bounds['alpha'][0] + (bounds['alpha'][1]-bounds['alpha'][0])*cube[cube_index['alpha']]
                else:
                    # default: 1 - 2
                    alpha = 1. + cube[cube_index['alpha']]

                cube[cube_index['alpha']] = alpha

                # photospheric pressure (bar)
                # default: 1e-3 - 1e2 bar
                p_phot = 10.**(-3. + 5.*cube[cube_index['log_delta']])

                # delta: proportionality factor in tau = delta * press_cgs**alpha
                # see Eq. 1 in Mollière et al. (2020)
                delta = (p_phot*1e6)**(-alpha)
                log_delta = np.log10(delta)

                cube[cube_index['log_delta']] = log_delta

            elif pt_profile == 'free':
                # 15 temperature (K) knots
                for i in range(15):
                    # default: 0 - 8000 K
                    cube[cube_index[f't{i}']] = 8000.*cube[cube_index[f't{i}']]

                # penalization of wiggles in the P-T profile
                # inverse Gamma: a=1, b=5e-5
                beta_r = cube[cube_index['beta_r']]
                gamma_r = invgamma.ppf(cube[cube_index['gamma_r']], a=1., scale=beta_r)
                cube[cube_index['beta_r']] = beta_r
                cube[cube_index['gamma_r']] = gamma_r

            elif pt_profile == 'monotonic':
                # 15 temperature (K) knots
                cube[cube_index['t14']] = 10000.*cube[cube_index['t14']]

                for i in range(13, -1, -1):
                    cube[cube_index[f't{i}']] = cube[cube_index[f't{i+1}']] * (1.-cube[cube_index[f't{i}']])

            if chemistry == 'equilibrium':
                # metallicity (dex) for the nabla_ad interpolation
                if 'metallicity' in bounds:
                    metallicity = bounds['metallicity'][0] + (bounds['metallicity'][1]-bounds['metallicity'][0])*cube[cube_index['metallicity']]
                else:
                    # default: -1.5 - 1.5 dex
                    metallicity = -1.5 + 3.*cube[cube_index['metallicity']]

                cube[cube_index['metallicity']] = metallicity

                # carbon-to-oxygen ratio for the nabla_ad interpolation
                if 'c_o_ratio' in bounds:
                    c_o_ratio = bounds['c_o_ratio'][0] + (bounds['c_o_ratio'][1]-bounds['c_o_ratio'][0])*cube[cube_index['c_o_ratio']]
                else:
                    # default: 0.1 - 1.6
                    c_o_ratio = 0.1 + 1.5*cube[cube_index['c_o_ratio']]

                cube[cube_index['c_o_ratio']] = c_o_ratio

            elif chemistry == 'free':
                # log10 abundances of the line species
                log_x_abund = {}

                for item in self.line_species:
                    if item in bounds:
                        cube[cube_index[item]] = bounds[item][0] + (bounds[item][1]-bounds[item][0])*cube[cube_index[item]]

                    elif item not in ['K', 'K_lor_cut', 'K_burrows']:
                        # default: -10. - 0. dex
                        cube[cube_index[item]] = -10.*cube[cube_index[item]]

                        # add the log10 of the mass fraction to the abundace dictionary
                        log_x_abund[item] = cube[cube_index[item]]

                if 'Na' in self.line_species or 'Na_lor_cut' in self.line_species or \
                        'Na_burrows' in self.line_species:
                    log_x_k_abund = retrieval_util.potassium_abundance(log_x_abund)

                if 'K' in self.line_species:
                    cube[cube_index['K']] = log_x_k_abund

                elif 'K_lor_cut' in self.line_species:
                    cube[cube_index['K_lor_cut']] = log_x_k_abund

                elif 'K_burrows' in self.line_species:
                    cube[cube_index['K_burrows']] = log_x_k_abund

            # quench pressure (bar)
            # default: 1e-6 - 1e3 bar
            if quenching:
                if 'log_p_quench' in bounds:
                    log_p_quench = bounds['log_p_quench'][0] + (bounds['log_p_quench'][1]-bounds['log_p_quench'][0])*cube[cube_index['log_p_quench']]
                else:
                    # default: -6 - 3.
                    log_p_quench = -6. + 9.*cube[cube_index['log_p_quench']]

                cube[cube_index['log_p_quench']] = log_p_quench

            if len(self.cloud_species) > 0:
                if 'Fe(c)' in self.cloud_species:
                    # cloud base mass fractions of Fe
                    # relative to the maximum values allowed from elemental abundances
                    # see Eq. 3 in Mollière et al. (2020)
                    # default: 0.05 - 1.
                    fe_fraction = np.log10(0.05)+(np.log10(1.)-np.log10(0.05))*cube[cube_index['fe_fraction']]
                    cube[cube_index['fe_fraction']] = fe_fraction

                if 'MgSiO3(c)' in self.cloud_species:
                    # cloud base mass fractions of MgSiO3
                    # relative to the maximum values allowed from elemental abundances
                    # see Eq. 3 in Mollière et al. (2020)
                    # default: 0.05 - 1.
                    mgsio3_fraction = np.log10(0.05)+(np.log10(1.)-np.log10(0.05))*cube[cube_index['mgsio3_fraction']]
                    cube[cube_index['mgsio3_fraction']] = mgsio3_fraction

                if 'Na2S(c)' in self.cloud_species:
                    # cloud base mass fractions of Na2S
                    # relative to the maximum values allowed from elemental abundances
                    # see Eq. 3 in Mollière et al. (2020)
                    # default: 0.05 - 1.
                    na2s_fraction = np.log10(0.05)+(np.log10(1.)-np.log10(0.05))*cube[cube_index['na2s_fraction']]
                    cube[cube_index['na2s_fraction']] = na2s_fraction

                if 'KCL(c)' in self.cloud_species:
                    # cloud base mass fractions of KCl
                    # relative to the maximum values allowed from elemental abundances
                    # see Eq. 3 in Mollière et al. (2020)
                    # default: 0.05 - 1.
                    kcl_fraction = np.log10(0.05)+(np.log10(1.)-np.log10(0.05))*cube[cube_index['kcl_fraction']]
                    cube[cube_index['kcl_fraction']] = kcl_fraction

                # sedimentation parameter
                # ratio of the settling and mixing velocities of the cloud particles
                # see Eq. 3 in Mollière et al. (2020)
                if 'fsed' in bounds:
                    fsed = bounds['fsed'][0] + (bounds['fsed'][1]-bounds['fsed'][0])*cube[cube_index['fsed']]
                else:
                    # default: 0 - 10
                    fsed = 10.*cube[cube_index['fsed']]

                cube[cube_index['fsed']] = fsed

                # eddy diffusion coefficient, log(Kzz)
                if 'kzz' in bounds:
                    kzz = bounds['kzz'][0] + (bounds['kzz'][1]-bounds['kzz'][0])*cube[cube_index['kzz']]
                else:
                    # default: 5 - 13
                    kzz = 5. + 8.*cube[cube_index['kzz']]

                cube[cube_index['kzz']] = kzz

                # width of the log-normal particle size distribution
                if 'sigma_lnorm' in bounds:
                    sigma_lnorm = bounds['sigma_lnorm'][0] + (bounds['sigma_lnorm'][1] -
                                                              bounds['sigma_lnorm'][0])*cube[cube_index['sigma_lnorm']]
                else:
                    # default: 1.05 - 3.
                    sigma_lnorm = 1.05 + 1.95*cube[cube_index['sigma_lnorm']]

                cube[cube_index['sigma_lnorm']] = sigma_lnorm

            # add flux scaling parameter if the boundaries are provided

            for item in self.spectrum:
                if item in bounds:
                    if bounds[item][0] is not None:
                        cube[cube_index[f'scaling_{item}']] = bounds[item][0][0] + \
                            (bounds[item][0][1]-bounds[item][0][0])*cube[cube_index[f'scaling_{item}']]

            # add error inflation parameter if the boundaries are provided

            for item in self.spectrum:
                if item in bounds:
                    if bounds[item][1] is not None:
                        cube[cube_index[f'error_{item}']] = bounds[item][1][0] + \
                            (bounds[item][1][1]-bounds[item][1][0]) * \
                            cube[cube_index[f'error_{item}']]

            # add wavelength calibration parameter if the boundaries are provided

            for item in self.spectrum:
                if item in bounds:
                    if bounds[item][2] is not None:
                        cube[cube_index[f'wavelength_{item}']] = bounds[item][2][0] + \
                            (bounds[item][2][1]-bounds[item][2][0]) * \
                            cube[cube_index[f'wavelength_{item}']]

        @typechecked
        def loglike(cube,
                    n_dim: int,
                    n_param: int) -> float:
            """
            Function for the logarithm of the likelihood, computed from the parameter cube.

            Parameters
            ----------
            cube : LP_c_double
                Unit cube.
            n_dim : int
                Number of dimensions.
            n_param : int
                Number of parameters.

            Returns
            -------
            float
                Sum of the logarithm of the prior and likelihood.
            """

            # initiate the logarithm of the prior and likelihood

            log_prior = 0.
            log_likelihood = 0.

            # create dictionary with flux scaling parameters

            scaling = {}

            for item in self.spectrum:
                if item in bounds and bounds[item][0] is not None:
                    scaling[item] = cube[cube_index[f'scaling_{item}']]
                else:
                    scaling[item] = 1.

            # create dictionary with error offset parameters

            err_offset = {}

            for item in self.spectrum:
                if item in bounds and bounds[item][1] is not None:
                    err_offset[item] = cube[cube_index[f'error_{item}']]
                else:
                    err_offset[item] = -100.

            # create dictionary with wavelength calibration parameters

            wavel_cal = {}

            for item in self.spectrum:
                if item in bounds and bounds[item][2] is not None:
                    wavel_cal[item] = cube[cube_index[f'wavelength_{item}']]
                else:
                    wavel_cal[item] = 0.

            # create a p-t profile

            if pt_profile == 'molliere':
                temp, _, _ = retrieval_util.pt_ret_model(np.array([cube[cube_index['t1']],
                                                                   cube[cube_index['t2']],
                                                                   cube[cube_index['t3']]]),
                                                         10.**cube[cube_index['log_delta']],
                                                         cube[cube_index['alpha']],
                                                         cube[cube_index['tint']],
                                                         self.pressure,
                                                         cube[cube_index['metallicity']],
                                                         cube[cube_index['c_o_ratio']])

            elif pt_profile in ['free', 'monotonic']:
                knot_temp = []
                for i in range(15):
                    knot_temp.append(cube[cube_index[f't{i}']])

                knot_temp = np.asarray(knot_temp)

                temp = retrieval_util.pt_spline_interp(knot_press, knot_temp, self.pressure)

                if pt_profile == 'free':
                    temp_sum = np.sum((knot_temp[2:] + knot_temp[:-2] - 2.*knot_temp[1:-1])**2.)
                    # temp_sum = np.sum((temp[::3][2:] + temp[::3][:-2] - 2.*temp[::3][1:-1])**2.)

                    log_prior += -1.*temp_sum/(2.*cube[cube_index['gamma_r']]) - \
                        0.5*np.log(2.*np.pi*cube[cube_index['gamma_r']])

            # return zero probability if the minimum temperature is negative

            if np.min(temp) < 0.:
                return -np.inf

            # set the quenching pressure
            if quenching:
                log_p_quench = cube[cube_index['log_p_quench']]
            else:
                log_p_quench = -10.

            # calculate the emission spectrum

            if len(self.cloud_species) > 0:
                # cloudy atmosphere

                cloud_fractions = {}
                for item in self.cloud_species:
                    cloud_fractions[item] = cube[cube_index[f'{item[:-3].lower()}_fraction']]

                log_x_base = retrieval_util.log_x_cloud_base(cube[cube_index['c_o_ratio']],
                                                                  cube[cube_index['metallicity']],
                                                                  cloud_fractions)

                # the try-except is required to catch numerical precision errors with the clouds
                # try:
                wlen_micron, flux_lambda, _ = retrieval_util.calc_spectrum_clouds(
                    rt_object, self.pressure, temp, cube[cube_index['c_o_ratio']], cube[cube_index['metallicity']], log_p_quench,
                    log_x_base, cube[cube_index['fsed']], cube[cube_index['kzz']], cube[cube_index['logg']],
                    cube[cube_index['sigma_lnorm']], chemistry=chemistry, half=True, plotting=plotting, contribution=False)

                # except:
                #     return -np.inf

            else:
                # clear atmosphere

                if chemistry == 'equilibrium':
                    wlen_micron, flux_lambda, _ = retrieval_util.calc_spectrum_clear(
                        rt_object, self.pressure, temp, cube[cube_index['logg']],
                        cube[cube_index['c_o_ratio']], cube[cube_index['metallicity']], log_p_quench,
                        None, chemistry=chemistry, half=True, contribution=False)

                elif chemistry == 'free':
                    # create a dictionary with the mass fractions
                    log_x_abund = {}
                    for item in self.line_species:
                        log_x_abund[item] = cube[cube_index[item]]

                    # check if the sum of fractional abundances is smaller than unity

                    if np.sum(10.**np.asarray(list(log_x_abund.values()))) > 1.:
                        return -np.inf

                    # check if the C/H and O/H ratios are within the prior boundaries

                    if 'c_h_ratio' or 'o_h_ratio' in bounds:
                        c_h_ratio, o_h_ratio = retrieval_util.calc_metal_ratio(log_x_abund)

                    if 'c_h_ratio' in bounds and (c_h_ratio < bounds['c_h_ratio'][0] or
                                                  c_h_ratio > bounds['c_h_ratio'][1]):

                        return -np.inf

                    if 'o_h_ratio' in bounds and (o_h_ratio < bounds['o_h_ratio'][0] or
                                                  o_h_ratio > bounds['o_h_ratio'][1]):

                        return -np.inf

                    # calculate the emission spectrum

                    wlen_micron, flux_lambda, _ = retrieval_util.calc_spectrum_clear(
                        rt_object, self.pressure, temp, cube[cube_index['logg']],
                        None, None, None, log_x_abund, chemistry, half=True, contribution=False)

            # return zero probability if the spectrum contains NaN values

            if np.sum(np.isnan(flux_lambda)) > 0:
                if len(flux_lambda) > 1:
                    warnings.warn('Spectrum with NaN values encountered.')

                return -np.inf

            # scale the emitted spectrum to the observation
            flux_lambda *= (cube[cube_index['radius']]*constants.R_JUP / (self.distance*constants.PARSEC))**2.

            for key, value in self.spectrum.items():
                # get spectrum
                # shift the wavelengths of the data with the fitted calibration parameter
                data_wavel = value[0][:, 0] + wavel_cal[key]
                data_flux = value[0][:, 1]
                data_error = value[0][:, 2]

                # get inverted covariance matrix
                data_cov_inv = value[2]

                # get spectral resolution
                spec_res = value[3]

                # get wavelength bins
                data_wavel_bins = value[4]

                # fitted error component
                err_fit = 10.**err_offset[key]

                # convolve with Gaussian LSF
                flux_smooth = retrieval_util.convolve(wlen_micron,
                                                      flux_lambda,
                                                      spec_res)

                # resample to the observation
                flux_rebinned = rebin_give_width(wlen_micron,
                                                 flux_smooth,
                                                 data_wavel,
                                                 data_wavel_bins)

                # difference between the observed and modeled spectrum
                diff = flux_rebinned - scaling[key]*data_flux

                if data_cov_inv is not None:
                    # calculate the log-likelihood with the covariance matrix
                    # TODO include err_fit in the covariance matrix
                    log_likelihood += -np.dot(diff, data_cov_inv.dot(diff))/2.

                else:
                    # calculate the log-likelihood without the covariance matrix
                    var_infl = data_error**2.+err_fit**2
                    log_likelihood += -0.5*np.sum(diff**2/var_infl + np.log(2.*np.pi*var_infl))

                if plotting:
                    plt.errorbar(data_wavel, scaling[key]*data_flux, yerr=data_error+err_fit,
                                 marker='o', ms=3, color='tab:blue', markerfacecolor='tab:blue')

                    plt.plot(data_wavel, flux_rebinned, marker='o', ms=3, color='tab:orange')

            if plotting:
                plt.plot(wlen_micron, flux_smooth, color='black', zorder=-20)
                plt.xlabel(r'Wavelength ($\mu$m)')
                plt.ylabel(r'Flux (W m$^{-2}$ $\mu$m$^{-1}$)')
                plt.savefig('spectrum.pdf', bbox_inches='tight')
                plt.clf()

            return log_prior + log_likelihood

        # store the model parameters in a JSON file

        json_filename = os.path.join(self.output_folder, 'params.json')
        print(f'Storing the model parameters: {json_filename}')

        with open(json_filename, 'w') as json_file:
            json.dump(self.parameters, json_file)

        # store the Radtrans arguments in a JSON file

        radtrans_filename = os.path.join(self.output_folder, 'radtrans.json')
        print(f'Storing the Radtrans arguments: {radtrans_filename}')

        radtrans_dict = {}
        radtrans_dict['line_species'] = self.line_species
        radtrans_dict['cloud_species'] = self.cloud_species
        radtrans_dict['distance'] = self.distance
        radtrans_dict['scattering'] = self.scattering
        radtrans_dict['chemistry'] = chemistry
        radtrans_dict['quenching'] = quenching
        radtrans_dict['pt_profile'] = pt_profile

        with open(radtrans_filename, 'w', encoding='utf-8') as json_file:
            json.dump(radtrans_dict, json_file, ensure_ascii=False, indent=4)

        # run the nested sampling with MultiNest

        print('Sampling the posterior distribution with MultiNest...')

        pymultinest.run(loglike,
                        prior,
                        len(self.parameters),
                        outputfiles_basename=os.path.join(self.output_folder, ''),
                        resume=resume,
                        verbose=True,
                        const_efficiency_mode=True,
                        sampling_efficiency=0.05,
                        n_live_points=live_points,
                        evidence_tolerance=0.5)