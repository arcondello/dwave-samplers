# Copyright 2018 D-Wave Systems Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS F ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A dimod :term:`sampler` that uses the MST2 multistart tabu search algorithm."""


import random
import warnings
import itertools
from functools import partial

import numpy as np
import dimod

from tabu import TabuSearch

__all__ = ["TabuSampler"]

class TabuSampler(dimod.Sampler, dimod.Initialized):
    """A tabu-search sampler.

    Examples:
        This example solves a two-variable Ising model.

        >>> from tabu import TabuSampler
        >>> samples = TabuSampler().sample_ising({'a': -0.5, 'b': 1.0}, {'ab': -1})
        >>> list(samples.data()) # doctest: +SKIP
        [Sample(sample={'a': -1, 'b': -1}, energy=-1.5, num_occurrences=1)]
        >>> samples.first.energy
        -1.5

    """

    properties = None
    parameters = None

    def __init__(self):
        self.parameters = {'tenure': [],
                           'timeout': [],
                           'num_reads': [],
                           'init_solution': []}
        self.properties = {}

    def sample(self, bqm, initial_states=None, initial_states_generator='random',
               num_reads=None, seed=None, tenure=None, timeout=20, **kwargs):
        """Run Tabu search on a given binary quadratic model.

        Args:
            bqm (:class:`~dimod.BinaryQuadraticModel`):
                The binary quadratic model (BQM) to be sampled.

            initial_states (:class:`~dimod.SampleSet`, optional, default=None):
                One or more samples, each defining an initial state for all the
                problem variables. Initial states are given one per read, but
                if fewer than `num_reads` initial states are defined, additional
                values are generated as specified by `initial_states_generator`.

            initial_states_generator (str, 'none'/'tile'/'random', optional, default='random'):
                Defines the expansion of `initial_states` if fewer than
                `num_reads` are specified:

                * "none":
                    If the number of initial states specified is smaller than
                    `num_reads`, raises ValueError.

                * "tile":
                    Reuses the specified initial states if fewer than `num_reads`
                    or truncates if greater.

                * "random":
                    Expands the specified initial states with randomly generated
                    states if fewer than `num_reads` or truncates if greater.

            num_reads (int, optional, default=len(initial_states) or 1):
                Number of reads. Each read is generated by one run of the tabu
                algorithm. If `num_reads` is not explicitly given, it is selected
                to match the number of initial states given. If initial states
                are not provided, only one read is performed.

            seed (int (32-bit unsigned integer), optional):
                Seed to use for the PRNG. Specifying a particular seed with a
                constant set of parameters produces identical results. If not
                provided, a random seed is chosen.
            
            tenure (int, optional):
                Tabu tenure, which is the length of the tabu list, or number of recently
                explored solutions kept in memory.
                Default is a quarter of the number of problem variables up to
                a maximum value of 20.

            timeout (int, optional):
                Total running time in milliseconds.

            init_solution (:class:`~dimod.SampleSet`, optional):
                Deprecated. Alias for `initial_states`.

        Returns:
            :obj:`~dimod.SampleSet`: A `dimod` :obj:`.~dimod.SampleSet` object.

        Examples:
            This example samples a simple two-variable Ising model.

            >>> import dimod
            >>> bqm = dimod.BQM.from_ising({}, {'ab': 1})

            >>> import tabu
            >>> sampler = tabu.TabuSampler()

            >>> samples = sampler.sample(bqm)
            >>> samples.record[0].energy
            -1.0
        """

        if not bqm:
            return dimod.SampleSet.from_samples([], energy=0, vartype=bqm.vartype)

        if tenure is None:
            tenure = max(min(20, len(bqm) // 4), 0)
        if not isinstance(tenure, int):
            raise TypeError("'tenure' should be an integer in range [0, num_vars - 1]")
        if not 0 <= tenure < len(bqm):
            raise ValueError("'tenure' should be an integer in range [0, num_vars - 1]")

        if 'init_solution' in kwargs:
            warnings.warn(
                "'init_solution' is deprecated in favor of 'initial_states'.",
                DeprecationWarning)
            initial_states = kwargs.pop('init_solution')

        # Get initial_states in binary form
        parsed = self.parse_initial_states(bqm.binary, 
                                           initial_states=initial_states, 
                                           initial_states_generator=initial_states_generator, 
                                           num_reads=num_reads, 
                                           seed=seed)

        parsed_initial_states = np.ascontiguousarray(parsed.initial_states.record.sample)

        qubo, varorder = self._bqm_to_tabu_qubo(bqm.binary)

        # run Tabu search
        samples = np.empty((parsed.num_reads, len(bqm)), dtype=np.int8)

        rng = np.random.RandomState(seed)

        for ni, initial_state in enumerate(parsed_initial_states):
            seed_per_read = rng.randint(2**32, dtype=np.uint32)
            r = TabuSearch(qubo, initial_state, tenure, timeout, seed_per_read)
            samples[ni, :] = r.bestSolution()

        # we received samples in binary form, so convert if needed
        if bqm.vartype is dimod.SPIN:
            samples *= 2
            samples -= 1
        elif bqm.vartype is not dimod.BINARY:
            # sanity check
            raise ValueError("unknown vartype")

        return dimod.SampleSet.from_samples_bqm((samples, varorder), bqm=bqm)

    @staticmethod
    def _bqm_to_tabu_qubo(bqm):
        # construct dense matrix representation
        ldata, (irow, icol, qdata), offset, varorder = bqm.binary.to_numpy_vectors(return_labels=True)
        ud = np.zeros((len(bqm), len(bqm)), dtype=np.double)
        ud[np.diag_indices(len(bqm), 2)] = ldata
        ud[irow, icol] = qdata

        # Note: normally, conversion would be: `ud + ud.T - np.diag(np.diag(ud))`,
        # but the Tabu solver we're using requires slightly different qubo matrix.
        ud *= .5
        symm = ud + ud.T
        return symm, varorder
