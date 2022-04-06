import os
import shutil

import pytest
import numpy as np

import species
from species.util import test_util


class TestPlanck:
    def setup_class(self):
        self.limit = 1e-8
        self.test_path = os.path.dirname(__file__) + "/"

    def teardown_class(self):
        os.remove("species_database.hdf5")
        os.remove("species_config.ini")
        shutil.rmtree("data/")

    def test_species_init(self):
        test_util.create_config("./")
        species.SpeciesInit()

    def test_read_planck(self):
        read_planck = species.ReadPlanck(filter_name="MKO/NSFCam.J")
        assert read_planck.wavel_range == pytest.approx(
            (1.1308, 1.3812), rel=1e-6, abs=0.0
        )

        read_planck = species.ReadPlanck(wavel_range=(1.0, 5.0))
        assert read_planck.wavel_range == (1.0, 5.0)

    def test_get_spectrum(self):
        read_planck = species.ReadPlanck(filter_name="MKO/NSFCam.J")
        modelbox = read_planck.get_spectrum(
            {"teff": 2000.0, "radius": 1.0, "distance": 10.0}, 100.0
        )

        assert modelbox.model == "planck"
        assert modelbox.wavelength.shape == (204,)
        assert modelbox.flux.shape == (204,)

        assert np.sum(modelbox.wavelength) == pytest.approx(
            255.37728257033913, rel=self.limit, abs=0.0
        )
        assert np.sum(modelbox.flux) == pytest.approx(
            4.0434750652879004e-12, rel=self.limit, abs=0.0
        )

    def test_get_flux(self):
        read_planck = species.ReadPlanck(filter_name="MKO/NSFCam.J")

        # low relative precision because of filter profile precision
        flux = read_planck.get_flux({"teff": 2000.0, "radius": 1.0, "distance": 10.0})
        assert flux[0] == pytest.approx(1.9888885697002363e-14, rel=1e-4, abs=0.0)

        # low relative precision because of filter profile precision
        synphot = species.SyntheticPhotometry(filter_name="MKO/NSFCam.J")
        flux = read_planck.get_flux(
            {"teff": 2000.0, "radius": 1.0, "distance": 10.0}, synphot=synphot
        )
        assert flux[0] == pytest.approx(1.9888885697002363e-14, rel=1e-4, abs=0.0)
