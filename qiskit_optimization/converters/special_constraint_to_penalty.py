# This code is part of Qiskit.
#
# (C) Copyright IBM 2020, 2021.
#
# This code is licensed under the Apache License, Version 2.0. You may
# obtain a copy of this license in the LICENSE.txt file in the root directory
# of this source tree or at http://www.apache.org/licenses/LICENSE-2.0.
#
# Any modifications or derivative works of this code must retain this
# copyright notice, and modified files need to carry a notice indicating
# that they have been altered from the originals.

"""Converter to convert a problem with equality constraints to unconstrained with penalty terms."""

import copy
import logging
from math import fsum
from typing import Optional, cast, Union, Tuple, List

import numpy as np

from .quadratic_program_converter import QuadraticProgramConverter
from ..exceptions import QiskitOptimizationError
from ..problems.constraint import Constraint
from ..problems.quadratic_objective import QuadraticObjective
from ..problems.quadratic_program import QuadraticProgram
from ..problems.special_constraint import SpecialConstraint
from ..problems.variable import Variable

logger = logging.getLogger(__name__)


class SpecialConstraintToPenalty(QuadraticProgramConverter):
    """Convert a problem with only equality constraints to unconstrained with penalty terms."""

    #SPECIAL_CONSTRAINTS = (("s1",([1, 1], 'LE', 1),(""))
    def __init__(self, penalty: Optional[float] = None) -> None:
        """
        Args:
            penalty: Penalty factor to scale equality constraints that are added to objective.
                     If None is passed, a penalty factor will be automatically calculated on
                     every conversion.
        """
        self._src = None  # type: Optional[QuadraticProgram]
        self._dst = None  # type: Optional[QuadraticProgram]
        self.penalty = penalty  # type: Optional[float]

        self._qb = QuadraticProgram()
        self._qb.binary_var()
        self._qb.binary_var()
        self._qb.binary_var()
        self._special_constraints = (SpecialConstraint(self._qb, [1,1,0], '<=', 1, 1,[0,0,0], [[0, 1, 0],[0, 0, 0],[0, 0, 0]]), 
                                    SpecialConstraint(self._qb, [1,1,0], '>=', 1, 1,[-1,-1,0], [[0, 1, 0],[0, 0, 0],[0, 0, 0]]))


    def convert(self, problem: QuadraticProgram) -> QuadraticProgram:
        """Convert a problem with equality constraints into an unconstrained problem.

        Args:
            problem: The problem to be solved, that does not contain inequality constraints.

        Returns:
            The converted problem, that is an unconstrained problem.

        Raises:
            QiskitOptimizationError: If an inequality constraint exists.
        """

        # create empty QuadraticProgram model
        self._src = copy.deepcopy(problem)
        self._dst = QuadraticProgram(name=problem.name)

        # If no penalty was given, set the penalty coefficient by _auto_define_penalty()
        if self._should_define_penalty:
            penalty = self._auto_define_penalty()
        else:
            penalty = self._penalty

        # Set variables
        for x in self._src.variables:
            if x.vartype == Variable.Type.CONTINUOUS:
                self._dst.continuous_var(x.lowerbound, x.upperbound, x.name)
            elif x.vartype == Variable.Type.BINARY:
                self._dst.binary_var(x.name)
            elif x.vartype == Variable.Type.INTEGER:
                self._dst.integer_var(x.lowerbound, x.upperbound, x.name)
            else:
                raise QiskitOptimizationError('Unsupported vartype: {}'.format(x.vartype))

        # get original objective terms
        offset = self._src.objective.constant
        linear = self._src.objective.linear.to_dict()
        quadratic = self._src.objective.quadratic.to_dict()
        sense = self._src.objective.sense.value

        # convert linear constraints into penalty terms
        for constraint in self._src.linear_constraints:
            for special_constraint in self._special_constraints:
                if special_constraint.is_special_constraint(constraint):
                    print("***Special!***")
                    print (constraint._linear.to_dict())
                    # add constant penalty
                    offset += sense * penalty * special_constraint.penalty_constant

                    # add linear penalty
                    row = special_constraint.penalty_linear_expression.to_dict()                    
                    for j, coef in row.items():
                        linear[j] = linear.get(j, 0.0) + sense * penalty * coef

                    # add quadratic penalty
                    row = special_constraint.penalty_quadratic_expression.to_dict()
                    for j, coef in row.items():
                        quadratic[j] = quadratic.get(j, 0.0) + sense * penalty * coef

                    break
            else:
                print("+++ Not Special +++")
                print(constraint._linear.to_dict())
                self._dst.linear_constraint(constraint._linear.to_dict(), constraint._sense, constraint._rhs, constraint._name)

        # convert quadratic constraints into penalty terms
        for constraint in self._src.quadratic_constraints:
            # T.B.I.
            self._dst.quadratic_constraint(constraint._linear.to_dict(), constraint._quadratic.to_dict(), constraint._sense, constraint._rhs, constraint._name)

        if self._src.objective.sense == QuadraticObjective.Sense.MINIMIZE:
            self._dst.minimize(offset, linear, quadratic)
        else:
            self._dst.maximize(offset, linear, quadratic)

        # Update the penalty to the one just used
        self._penalty = penalty  # type: float

        return self._dst

    def _auto_define_penalty(self) -> float:
        """Automatically define the penalty coefficient.

        Returns:
            Return the minimum valid penalty factor calculated
            from the upper bound and the lower bound of the objective function.
            If a constraint has a float coefficient,
            return the default value for the penalty factor.
        """
        default_penalty = 1e5

        # Check coefficients of constraints.
        # If a constraint has a float coefficient, return the default value for the penalty factor.
        terms = []
        for constraint in self._src.linear_constraints:
            terms.append(constraint.rhs)
            terms.extend(coef for coef in constraint.linear.to_dict().values())
        if any(isinstance(term, float) and not term.is_integer() for term in terms):
            logger.warning(
                'Warning: Using %f for the penalty coefficient because '
                'a float coefficient exists in constraints. \n'
                'The value could be too small. '
                'If so, set the penalty coefficient manually.',
                default_penalty,
            )
            return default_penalty

        # (upper bound - lower bound) can be calculate as the sum of absolute value of coefficients
        # Firstly, add 1 to guarantee that infeasible answers will be greater than upper bound.
        penalties = [1.0]
        # add linear terms of the object function.
        penalties.extend(abs(coef) for coef in self._src.objective.linear.to_dict().values())
        # add quadratic terms of the object function.
        penalties.extend(abs(coef) for coef in self._src.objective.quadratic.to_dict().values())

        return fsum(penalties)

    def interpret(self, x: Union[np.ndarray, List[float]]) -> np.ndarray:
        """Convert the result of the converted problem back to that of the original problem

        Args:
            x: The result of the converted problem or the given result in case of FAILURE.

        Returns:
            The result of the original problem.

        Raises:
            QiskitOptimizationError: if the number of variables in the result differs from
                                     that of the original problem.
        """
        if len(x) != self._src.get_num_vars():
            raise QiskitOptimizationError(
                'The number of variables in the passed result differs from '
                'that of the original problem.'
            )
        return np.asarray(x)

    @property
    def penalty(self) -> Optional[float]:
        """Returns the penalty factor used in conversion.

        Returns:
            The penalty factor used in conversion.
        """
        return self._penalty

    @penalty.setter
    def penalty(self, penalty: Optional[float]) -> None:
        """Set a new penalty factor.

        Args:
            penalty: The new penalty factor.
                     If None is passed, a penalty factor will be automatically calculated
                     on every conversion.
        """
        self._penalty = penalty
        self._should_define_penalty = penalty is None  # type: bool
