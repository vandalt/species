"""
Module for BT-Settl atmospheric model spectra.
"""

import os
import tarfile
import urllib.request

from typing import Optional, Tuple

import h5py
import spectres
import numpy as np

from typeguard import typechecked

from species.util import data_util, read_util


@typechecked
def add_btsettl(input_path: str,
                database: h5py._hl.files.File,
                wavel_range: Optional[Tuple[float, float]],
                teff_range: Optional[Tuple[float, float]],
                spec_res: Optional[float]) -> None:
    """
    Function for adding the BT-Settl atmospheric models (solar metallicity) to the database.
    The spectra have been downloaded from the Theoretical spectra web server
    (http://svo2.cab.inta-csic.es/svo/theory/newov2/index.php?models=bt-settl) and resampled
    to a spectral resolution of 5000 from 0.1 to 100 um. For Teff > 2500 K, these are
    BT-NextGen spectra instead of BT-Settl.

    Parameters
    ----------
    input_path : str
        Folder where the data is located.
    database : h5py._hl.files.File
        Database.
    wavel_range : tuple(float, float), None
        Wavelength range (um). The original wavelength points are used if set to ``None``.
    teff_range : tuple(float, float), None
        Effective temperature range (K). All temperatures are selected if set to ``None``.
    spec_res : float, None
        Spectral resolution. Not used if ``wavel_range`` is set to ``None``.

    Returns
    -------
    NoneType
        None
    """

    if not os.path.exists(input_path):
        os.makedirs(input_path)

    input_file = 'bt-settl.tgz'

    data_folder = os.path.join(input_path, 'bt-settl/')
    data_file = os.path.join(input_path, input_file)

    if not os.path.exists(data_folder):
        os.makedirs(data_folder)

    url = 'https://people.phys.ethz.ch/~ipa/tstolker/bt-settl.tgz'

    if not os.path.isfile(data_file):
        print('Downloading Bt-Settl model spectra (227 MB)...', end='', flush=True)
        urllib.request.urlretrieve(url, data_file)
        print(' [DONE]')

    print('Unpacking BT-Settl model spectra (227 MB)...', end='', flush=True)
    tar = tarfile.open(data_file)
    tar.extractall(data_folder)
    tar.close()
    print(' [DONE]')

    teff = []
    logg = []
    flux = []

    if wavel_range is not None and spec_res is not None:
        wavelength = read_util.create_wavelengths(wavel_range, spec_res)
    else:
        wavelength = None

    for _, _, file_list in os.walk(data_folder):
        for filename in sorted(file_list):
            if filename[:9] == 'bt-settl_':
                file_split = filename.split('_')

                teff_val = float(file_split[2])
                logg_val = float(file_split[4])

                if teff_range is not None:
                    if teff_val < teff_range[0] or teff_val > teff_range[1]:
                        continue

                print_message = f'Adding BT-Settl model spectra... {filename}'
                print(f'\r{print_message:<69}', end='')

                data_wavel, data_flux = np.loadtxt(os.path.join(data_folder, filename), unpack=True)

                teff.append(teff_val)
                logg.append(logg_val)

                if wavel_range is None or spec_res is None:
                    if wavelength is None:
                        wavelength = np.copy(data_wavel)  # (um)

                    if np.all(np.diff(wavelength) < 0):
                        raise ValueError('The wavelengths are not all sorted by increasing value.')

                    flux.append(data_flux)  # (W m-2 um-1)

                else:
                    flux_resample = spectres.spectres(wavelength,
                                                      data_wavel,
                                                      data_flux,
                                                      spec_errs=None,
                                                      fill=np.nan,
                                                      verbose=False)

                    if np.isnan(np.sum(flux_resample)):
                        raise ValueError(f'Resampling is only possible if the new wavelength '
                                         f'range ({wavelength[0]} - {wavelength[-1]} um) falls '
                                         f'sufficiently far within the wavelength range '
                                         f'({data_wavel[0]} - {data_wavel[-1]} um) of the input '
                                         f'spectra.')

                    flux.append(flux_resample)  # (W m-2 um-1)

    print_message = 'Adding BT-Settl model spectra... [DONE]'
    print(f'\r{print_message:<69}')

    data_sorted = data_util.sort_data(np.asarray(teff),
                                      np.asarray(logg),
                                      None,
                                      None,
                                      None,
                                      wavelength,
                                      np.asarray(flux))

    data_util.write_data('bt-settl',
                         ['teff', 'logg'],
                         database,
                         data_sorted)
