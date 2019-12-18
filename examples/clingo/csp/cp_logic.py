import math
import clingo

MAX_INT = 20
MIN_INT = -20
OFFSET = 0-MIN_INT
TRUE_LIT = 1

def match(term, name, arity):
    return (term.type == clingo.TheoryTermType.Function and
            term.name == name and
            len(term.arguments) == arity)

class Constraint(object):
    def __init__(self, atom):
        self.rhs = self._parse_num(atom.guard[1])
        self.vars = list(self._parse_elems(atom.elements))

    def _parse_elems(self, elems):
        for elem in elems:
            if len(elem.terms) == 1 and len(elem.condition) == 0:
                # python 3 has yield from
                for x in self._parse_elem(elem.terms[0]):
                    yield x
            else:
                raise RuntimeError("Invalid Syntax")

    def _parse_elem(self, term):
        if match(term, "+", 2):
            for x in self._parse_elem(term.arguments[0]):
                yield x
            for x in self._parse_elem(term.arguments[1]):
                yield x
        elif match(term, "*", 2):
            num = self._parse_num(term.arguments[0])
            var = self._parse_var(term.arguments[1])
            yield num, var
        else:
            raise RuntimeError("Invalid Syntax")

    def _parse_num(self, term):
        if term.type == clingo.TheoryTermType.Number:
            return term.number
        elif (term.type == clingo.TheoryTermType.Symbol and
                term.symbol.type == clingo.SymbolType.Number):
            return term.symbol.number
        elif match(term, "-", 1):
            return -self._parse_num(term.arguments[0])
        elif match(term, "+", 1):
            return +self._parse_num(term.arguments[0])
        else:
            raise RuntimeError("Invalid Syntax")

    def _parse_var(self, term):
        return str(term)

    def __str__(self):
        return "{} <= {}".format(self.vars, self.rhs)


class VarState(object):
    def __init__(self):
        self._upper_bound = [MAX_INT]
        self._lower_bound = [MIN_INT]
        # FIXME: needs better container
        self.literals = (MAX_INT-MIN_INT+1)*[None]

    def push(self):
        self._lower_bound.append(self.lower_bound)
        self._upper_bound.append(self.upper_bound)

    def pop(self):
        self._lower_bound.pop()
        self._upper_bound.pop()

    @property
    def lower_bound(self):
        return self._lower_bound[-1]

    @lower_bound.setter
    def lower_bound(self, value):
        self._lower_bound[-1] = value

    @property
    def upper_bound(self):
        return self._upper_bound[-1]

    @upper_bound.setter
    def upper_bound(self, value):
        self._upper_bound[-1] = value

    def has_literal(self, value):
        return self.literals[value - MIN_INT] is not None

class State(object):
    def __init__(self):
        self._vars = []
        self._var_state = {}
        self._litmap = {}
        self._levels = [0]

    def state(self, var):
        return self._var_state[var]

    def add_literal(self, var, value, lit):
        self._litmap.setdefault(lit, []).append((var, value))
        self.state(var).literals[value - MIN_INT] = lit

    def get_literal(self, var, value, control):
        vs = self.state(var)
        if not vs.has_literal(value):
            lit = control.add_literal()
            self.add_literal(var, value, lit)
            control.add_watch(lit)
            control.add_watch(-lit)
        return vs.literals[value - MIN_INT]

    # initialization
    def init_domain(self, variables):
        self._vars = variables
        for v in self._vars:
            self._var_state[v] = VarState()
            self.add_literal(v, MAX_INT, TRUE_LIT)

    # propagation
    def propagate(self, dl, changes):
        assert self._levels[-1] <= dl
        if self._levels[-1] < dl:
            # FIXME: not much effort to make lazy
            for v in self._vars:
                self._var_state[v].push()
            self._levels.append(dl)
        for i in changes:
            self.update_domain(i)

    def update_domain(self, order_lit): #literal is always true, may need negation to be found
        if order_lit in self._litmap:
            for var, value in self._litmap[order_lit]:
                vs = self.state(var)
                if vs.upper_bound > value:
                    vs.upper_bound = value
        else:
            assert -order_lit in self._litmap
            for var, value in self._litmap[-order_lit]:
                vs = self.state(var)
                if vs.lower_bound < value+1:
                    vs.lower_bound = value+1

    def propagate_true(self, l, c, control):
        clauses = []
        for a, v in c.vars:
            bound = c.rhs
            lbs = []
            for x, var in c.vars:
                if (a, v) != (x, var):
                    vs = self.state(var)
                    if x > 0:
                        bound -= x*vs.lower_bound
                        if vs.lower_bound > MIN_INT:
                            assert vs.has_literal(vs.lower_bound-1)
                            lbs.append(self.get_literal(var, vs.lower_bound-1, control))

                    else:
                        bound -= x*vs.upper_bound
                        lbs.append(self.get_literal(var, vs.upper_bound, control))

            if a > 0:
                ub = max(min(int(math.floor(bound/a)),MAX_INT), MIN_INT)
                lit = self.get_literal(v, ub, control)
            else:
                lb = max(min(int(bound/a), MAX_INT), MIN_INT)
                lit = -self.get_literal(v, lb-1, control)

            if (not any(control.assignment.is_true(x) for x in lbs) and
                    not control.assignment.is_true(lit)):
                clauses.append([-l, lit]+lbs)
        return clauses

    def propagate_orderlits(self, control):
        for v in self._vars:
            vs = self.state(v)
            lb_lit = self.get_literal(v, vs.lower_bound-1, control)
            for value in range(MIN_INT+1, vs.lower_bound):
                if (vs.has_literal(value-1) and
                        not control.assignment.is_true(-self.get_literal(v, value-1, control)) and
                        not control.add_clause([lb_lit, -self.get_literal(v, value-1, control)])):
                    return False
            assert vs.has_literal(vs.upper_bound)
            ub_lit = self.get_literal(v, vs.upper_bound, control)
            for value in range(vs.upper_bound+1, MAX_INT):
                if (vs.has_literal(value) and
                        not control.assignment.is_true(self.get_literal(v, value, control)) and
                        not control.add_clause([-ub_lit, self.get_literal(v, value, control)])):
                    return False
        return True

    def undo(self):
        # FIXME: not much effort to make lazy
        for v in self._vars:
            self.state(v).pop()
        self._levels.pop()


class Propagator(object):
    def __init__(self):
        self.__l2c = {}    # {literal: [Constraint]}
        self.__c2l = {}    # {Constraint: [literal]}
        self.__states = [] # [threadId : State]
        self.__vars = []

    def __state(self, thread_id):
        while len(self.__states) <= thread_id:
            self.__states.append(State())
        return self.__states[thread_id]

    def init(self, init):
        init.check_mode = clingo.PropagatorCheckMode.Fixpoint

        variables = set()
        for atom in init.theory_atoms:
            term = atom.term
            if term.name == "sum" and len(term.arguments) == 0:
                c = Constraint(atom)
                lit = init.solver_literal(atom.literal)
                self.__l2c.setdefault(lit, []).append(c)
                self.__c2l.setdefault(c, []).append(lit)
                for _, v in c.vars:
                    variables.add(v)
        self.__vars = list(sorted(variables))

        for i in range(len(self.__states), init.number_of_threads):
            self.__state(i).init_domain(self.__vars)

    def propagate(self, control, changes):
        s = self.__state(control.thread_id)
        dl = control.assignment.decision_level
        s.propagate(dl, changes)

    def check(self, control):
        size = control.assignment.size
        state = self.__state(control.thread_id)
        if not state.propagate_orderlits(control):
            return
        # FIXME: do not iterate over hashtable when order matters!
        for l, constraints in self.__l2c.items(): # bad?
            for c in constraints:
                if control.assignment.is_true(l):
                    for clause in state.propagate_true(l, c, control):
                        if not control.add_clause(clause):
                            return
            if not control.propagate():
                return
            if not state.propagate_orderlits(control):
                return
        if size == control.assignment.size and control.assignment.is_total: # first condition ensures that wait for propagate first to update our domains
            self.check_full(control)

    def check_full(self, control):
        s = self.__state(control.thread_id)
        for v in self.__vars:
            vs = s.state(v)
            if vs.lower_bound != vs.upper_bound:
                l = control.add_literal()
                s.add_literal(v, vs.lower_bound+int(math.floor((vs.upper_bound-vs.lower_bound)/2)), l)
                control.add_watch(l)
                control.add_watch(-l)
                return

    def undo(self, thread_id, assign, changes):
        self.__state(thread_id).undo()

    def get_assignment(self, thread_id):
        s = self.__state(thread_id)
        vvs = ((x, s.state(x)) for x in self.__vars)
        return [(v, vs.lower_bound) for v, vs in vvs if vs.lower_bound == vs.upper_bound]
