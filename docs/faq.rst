.. _faq:

Frequently Asked Questions
==========================

What dependency versions should I use?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The ``species`` package is kept reasonably well up to date with recent dependency versions. If you encounter an error then try to update the dependencies.

For example, in your local folder where you may have cloned the repository:

.. code-block:: bash

   pip install --upgrade -e .

Which tools support multiprocessing?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The parameter inference tools in ``FitModel``, ``FitEvolution``, ``EmissionLine``, and ``AtmosphericRetrieval`` that make use of ``MultiNest``, ``UltraNest``, and ``Dynesty``.

How do I run my code on multiple CPUs?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

First, make sure to install ``mpi4py``:

.. code-block:: bash

   pip install mpi4py

Then, to execute you ``species`` script with MPI, for example using 8 CPUs:

.. code-block:: bash

   mpirun -n 8 python run_species.py

.. important::
   Writing to the HDF5 database is not possible with multiprocessing, whereas reading data is. It is important to store any needed data (e.g. companion data, atmospheric models) in the HDF5 database using a single CPU, before running a fit. Next, it is recommended to execute a minimal code with multiprocessing, e.g. only ``SpeciesInit``, ``FitModel``, and ``run_multinest``. The results from the fit will get stored in the database.

My fit does not converge, help!
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The reason could be related to the model, the data, or both. In general, it is best to start with a simple setup and gradually increase the complexity.

- Check whether the atmospheric model is appropriate for the data. For example, the available :math:`T_\mathrm{eff}` range may be inconsistent with the object, or a cloudless model may be used for an atmosphere that is strongly affected by clouds. In such cases, the fit will typically still converge, but the sampler may struggle.

- Try plotting the data together with a model spectrum before fitting (see `this tutorial <https://species.readthedocs.io/en/latest/tutorials/data_model.html>`_), to visually check what parameters, in particular ``teff`` and ``radius``, are to be expected from the fit. Or, you can run ``CompareSpectra`` (see `this tutorial <https://species.readthedocs.io/en/latest/tutorials/grid_comparison.html>`_), to check which spectrum of the model grid is the best match with the data, without interpolating the grid.

- Start with a modest number of live points. About 200 is sufficient for getting a decent posterior for testing purposes.

- Reduce the dimensionality of the model. For example, fix the metallicity and C/O ratio to solar values before fitting them as free parameters (see the `documentation <https://species.readthedocs.io/en/latest/species.fit.html#species.fit.fit_model.FitModel>`_ of ``FitModel``).

- Restrict the temperature range. Some atmospheric model grids cover a large parameter space, so it is useful to set a more restricting prior on :math:`T_\mathrm{eff}`. Otherwise, the sampler will explore the full range of temperatures available in the database. If you do not need the full temperature range of a model grid, you can also restrict it when adding the model data with the ``teff_range`` parameter of ``add_model()`` (see `documentation <https://species.readthedocs.io/en/latest/species.data.html#species.data.database.Database.add_model>`_).

- Check the data uncertainties. If the uncertainties are underestimated, possibly in combination with too restricting priors, then the fit will have difficulty to converge. In such cases, it may help to fit an inflation of the data uncertainties (see  `documentation <https://species.readthedocs.io/en/latest/species.fit.html#species.fit.fit_model.FitModel>`_).

- Check for flux calibration differences between instruments. It may help to include an additional scaling parameter for spectra if the absolute flux calibration may have been different between spectra from different instruments (see `documentation <https://species.readthedocs.io/en/latest/species.fit.html#species.fit.fit_model.FitModel>`_).
