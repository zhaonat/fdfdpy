import numpy as np
import scipy.sparse as sp
from copy import deepcopy

from fdfdpy.linalg import construct_A, solver_direct, grid_average
from fdfdpy.derivatives import unpack_derivs
from fdfdpy.plot import plt_base, plt_base_eps
from fdfdpy.nonlinear_solvers import born_solve, newton_solve
from fdfdpy.source.mode import mode
from fdfdpy.nonlinearity import Nonlinearity
from fdfdpy.constants import (DEFAULT_LENGTH_SCALE, DEFAULT_MATRIX_FORMAT,
                              DEFAULT_SOLVER, EPSILON_0, MU_0)


class Simulation:

    def __init__(self, omega, eps_r, dl, NPML, pol, L0=DEFAULT_LENGTH_SCALE):
        # initializes Fdfd object

        self.L0 = L0
        self.omega = float(omega)
        self.dl = float(dl)
        self.NPML = [int(n) for n in NPML]
        self.pol = pol

        self._check_inputs()

        (Nx, Ny) = eps_r.shape
        self.Nx = Nx
        self.Ny = Ny
        self.mu_r = np.ones((self.Nx, self.Ny))
        self.src = np.zeros((self.Nx, self.Ny))
        self.xrange = [0, float(Nx*self.dl)]
        self.yrange = [0, float(Ny*self.dl)]

        # construct the system matrix
        self.eps_r = eps_r

        self.modes = []
        self.nonlinearity = []
        self.eps_nl = np.zeros(eps_r.shape)
        self.dnl_de = np.zeros(eps_r.shape)
        self.dnl_deps = np.zeros(eps_r.shape)

    def setup_modes(self):
        # calculates
        for modei in self.modes:
            modei.setup_src(self)

    def add_mode(self, neff, direction_normal, center, width,
                 scale=1, order=1):
        # adds a mode definition to the simulation
        new_mode = mode(neff, direction_normal, center, width,
                        scale=scale, order=order)
        self.modes.append(new_mode)

    def compute_nl(self, e, matrix_format=DEFAULT_MATRIX_FORMAT):
        # evaluates the nonlinear functions for a field e
        self.eps_nl = np.zeros(self.eps_r.shape)
        self.dnl_de = np.zeros(self.eps_r.shape)
        self.dnl_deps = np.zeros(self.eps_r.shape)
        for nli in self.nonlinearity:
            self.eps_nl = self.eps_nl + nli.eps_nl(e, self.eps_r)
            self.dnl_de = self.dnl_de + nli.dnl_de(e, self.eps_r)
            self.dnl_deps = self.dnl_deps + nli.dnl_deps(e, self.eps_r)
        Nbig = self.Nx*self.Ny
        Anl = sp.spdiags(self.omega**2*EPSILON_0*self.L0*self.eps_nl.reshape((-1,)), 0, Nbig, Nbig, format=matrix_format)
        self.Anl = Anl

    def add_nl(self, chi, nl_region, nl_type='kerr', eps_scale=False, eps_max=None):
        # adds a nonlinearity to the simulation
        new_nl = Nonlinearity(chi/np.square(self.L0), nl_region, nl_type, eps_scale, eps_max)
        self.nonlinearity.append(new_nl)

    @property
    def eps_r(self):
        return self.__eps_r

    @eps_r.setter
    def eps_r(self, new_eps):
        self.__eps_r = new_eps
        (A, derivs) = construct_A(self.omega, self.xrange, self.yrange,
                                  self.eps_r, self.NPML, self.pol, self.L0,
                                  matrix_format=DEFAULT_MATRIX_FORMAT,
                                  timing=False)
        self.A = A
        self.derivs = derivs
        self.fields = {f: None for f in ['Ex', 'Ey', 'Ez', 'Hx', 'Hy', 'Hz']}

    def reset_eps(self, new_eps):
        # in here for compatibility for now..

        self.eps_r = new_eps
        (A, derivs) = construct_A(self.omega, self.xrange, self.yrange, self.eps_r, self.NPML, self.pol, self.L0,
                                matrix_format=DEFAULT_MATRIX_FORMAT,
                                timing=False)
        self.A = A
        self.derivs = derivs
        self.fields = {f: None for f in ['Ex', 'Ey', 'Ez', 'Hx', 'Hy', 'Hz']}

    def compute_index_shift(self):
        """ Computes array of nonlinear refractive index shift"""

        _ = self.solve_fields()
        _ = self.solve_fields_nl()
        index_nl = np.sqrt(np.real(self.eps_r + self.eps_nl))
        index_lin = np.sqrt(np.real(self.eps_r))
        return np.abs(index_nl - index_lin)

    def solve_fields(self, include_nl=False, timing=False, averaging=True, solver=DEFAULT_SOLVER,
                     matrix_format=DEFAULT_MATRIX_FORMAT):
        # performs direct solve for A given source

        EPSILON_0_ = EPSILON_0*self.L0
        MU_0_ = MU_0*self.L0

        if include_nl==False:
            eps_tot = self.eps_r
            X = solver_direct(self.A, self.src*1j*self.omega, timing=timing,
                solver=solver)
        else:
            eps_tot = self.eps_r + self.eps_nl
            X = solver_direct(self.A + self.Anl, self.src*1j*self.omega, timing=timing,
                solver=solver)


        (Nx, Ny) = self.src.shape
        M = Nx*Ny
        (Dyb, Dxb, Dxf, Dyf) = unpack_derivs(self.derivs)

        if self.pol == 'Hz':
            if averaging:
                eps_x = grid_average(EPSILON_0_*(eps_tot), 'x')
                vector_eps_x = eps_x.reshape((-1,))
                eps_y = grid_average(EPSILON_0_*(eps_tot), 'y')
                vector_eps_y = eps_y.reshape((-1,))
            else:
                vector_eps_x = EPSILON_0_*(eps_tot).reshape((-1,))
                vector_eps_y = EPSILON_0_*(eps_tot).reshape((-1,))

            T_eps_x_inv = sp.spdiags(1/vector_eps_x, 0, M, M,
                                  format=matrix_format)
            T_eps_y_inv = sp.spdiags(1/vector_eps_y, 0, M, M,
                                  format=matrix_format)

            ex = 1/1j/self.omega * T_eps_y_inv.dot(Dyb).dot(X)
            ey = -1/1j/self.omega * T_eps_x_inv.dot(Dxb).dot(X)

            Ex = ex.reshape((Nx, Ny))
            Ey = ey.reshape((Nx, Ny))
            Hz = X.reshape((Nx, Ny))

            self.fields['Ex'] = Ex
            self.fields['Ey'] = Ey
            self.fields['Hz'] = Hz

            return (Ex, Ey, Hz)

        elif self.pol == 'Ez':
            hx = -1/1j/self.omega/MU_0_ * Dyb.dot(X)
            hy = 1/1j/self.omega/MU_0_ * Dxb.dot(X)

            Hx = hx.reshape((Nx, Ny))
            Hy = hy.reshape((Nx, Ny))
            Ez = X.reshape((Nx, Ny))

            self.fields['Hx'] = Hx
            self.fields['Hy'] = Hy
            self.fields['Ez'] = Ez

            return (Hx, Hy, Ez)

        else:
            raise ValueError('Invalid polarization: {}'.format(str(self.pol)))

    def solve_fields_nl(self,
                        timing=False, averaging=True,
                        Estart=None, solver_nl='newton', conv_threshold=1e-10,
                        max_num_iter=50, solver=DEFAULT_SOLVER,
                        matrix_format=DEFAULT_MATRIX_FORMAT):
        # solves for the nonlinear fields of the simulation.

        if self.pol == 'Ez':
            if solver_nl == 'born':
                (Hx, Hy, Ez, conv_array) = born_solve(self, Estart,
                                                      conv_threshold,
                                                      max_num_iter,
                                                      averaging=averaging)
            elif solver_nl == 'newton':
                (Hx, Hy, Ez, conv_array) = newton_solve(self, Estart,
                                                        conv_threshold,
                                                        max_num_iter,
                                                        averaging=averaging)
            elif solver_nl == 'LM':
                (Hx, Hy, Ez, conv_array) = LM_solve(self, Estart,
                                                    conv_threshold,
                                                    max_num_iter,
                                                    averaging=averaging)
            # incorrect solver_nl argument
            else:
                raise AssertionError("solver must be one of "
                                     "{'born', 'newton', 'LM'}")

            # return final nonlinear fields and an array of the convergences

            self.fields['Hx'] = Hx
            self.fields['Hy'] = Hy
            self.fields['Ez'] = Ez

            return (Hx, Hy, Ez, conv_array)

        elif self.pol == 'Hz':
            # if born solver
            if solver_nl == 'born':

                (Ex, Ey, Hz, conv_array) = born_solve(self, Estart,
                                                      conv_threshold,
                                                      max_num_iter,
                                                      averaging=averaging)

            # if newton solver
            elif solver_nl == 'newton':

                (Ex, Ey, Hz, conv_array) = newton_solve(self,
                                                        Estart, conv_threshold,
                                                        max_num_iter,
                                                        averaging=averaging)

            # incorrect solver_nl argument
            else:
                raise AssertionError("solver must be one of "
                                     "{'born', 'newton'}")

            # return final nonlinear fields and an array of the convergences

            self.fields['Ex'] = Ex
            self.fields['Ey'] = Ey
            self.fields['Hz'] = Hz

            return (Ex, Ey, Hz, conv_array)

        else:
            raise ValueError('Invalid polarization: {}'.format(str(self.pol)))

    def _check_inputs(self):
        # checks the inputs and makes sure they are kosher

        assert self.L0 > 0, "L0 must be a positive number, was supplied {},".format(str(self.L0))
        assert len(self.NPML) == 2, "yrange must be a list of length 2, was supplied {}, which is of length {}".format(str(self.NPML), len(self.NPML))
        assert self.NPML[0] >= 0 and self.NPML[1] >= 0, "both elements of NPML must be >= 0"

        assert self.pol in ['Ez', 'Hz'], "pol must be one of 'Ez' or 'Hz'"

        # to do, check for correct types as well.

    def flux_probe(self, direction_normal, center, width):
        # computes the total flux across the plane (line in 2D) defined by direction_normal, center, width

        # first extract the slice of the permittivity
        if direction_normal == "x":
            inds_x = [center[0], center[0]+1]
            inds_y = [int(center[1]-width/2), int(center[1]+width/2)]
        elif direction_normal == "y":
            inds_x = [int(center[0]-width/2), int(center[0]+width/2)]
            inds_y = [center[1], center[1]+1]
        else:
            raise ValueError("The value of direction_normal is neither x nor y!")

        if self.pol == 'Ez':
            Ez_x = grid_average(self.fields['Ez'][inds_x[0]:inds_x[1]+1, inds_y[0]:inds_y[1]+1], 'x')[:-1,:-1]
            Ez_y = grid_average(self.fields['Ez'][inds_x[0]:inds_x[1]+1, inds_y[0]:inds_y[1]+1], 'y')[:-1,:-1]
            # NOTE: Last part drops the extra rows/cols used for grid_average

            if direction_normal == "x":
                Sx = -1/2*np.real(Ez_x*np.conj(self.fields['Hy'][inds_x[0]:inds_x[1], inds_y[0]:inds_y[1]]))
                return self.dl*np.sum(Sx)
            elif direction_normal == "y":
                Sy = 1/2*np.real(Ez_y*np.conj(self.fields['Hy'][inds_x[0]:inds_x[1], inds_y[0]:inds_y[1]]))
                return self.dl*np.sum(Sy)

        elif self.pol == 'Hz':
            Hz_x = grid_average(self.fields['Hz'][inds_x[0]:inds_x[1]+1, inds_y[0]:inds_y[1]+1], 'x')[:-1, :-1]
            Hz_y = grid_average(self.fields['Hz'][inds_x[0]:inds_x[1]+1, inds_y[0]:inds_y[1]+1], 'y')[:-1, :-1]
            # NOTE: Last part drops the extra rows/cols used for grid_average

            if direction_normal == "x":
                Sx = 1/2*np.real(self.fields['Ey'][inds_x[0]:inds_x[1], inds_y[0]:inds_y[1]]*np.conj(Hz_x))
                return self.dl*np.sum(Sx)
            elif direction_normal == "y":
                Sy = -1/2*np.real(self.fields['Ex'][inds_x[0]:inds_x[1], inds_y[0]:inds_y[1]]*np.conj(Hz_y))
                return self.dl*np.sum(Sy)

    def plt_abs(self, cbar=True, outline=True, ax=None, vmax=None, tiled_y=1):
        # plot np.absolute value of primary field (e.g. Ez/Hz)

        if self.fields[self.pol] is None:
            raise ValueError("need to solve the simulation first")

        eps_r = self.eps_r
        eps_r = np.hstack(tiled_y*[eps_r])

        field_val = np.abs(self.fields[self.pol])
        field_val = np.hstack(tiled_y*[field_val])

        outline_val = np.abs(eps_r)
        vmin = 0.0

        if vmax is None:
            vmax = field_val.max()

        cmap = "magma"

        return plt_base(field_val, outline_val, cmap, vmin, vmax, self.pol,
                        cbar=cbar, outline=outline, ax=ax)

    def init_design_region(self, design_region, eps_m, style=''):
        """ Initializes the design_region permittivity depending on style"""

        if style == 'full':
            # eps_m filled in design region
            eps_full = eps_m * np.ones(self.eps_r.shape)
            eps_full[design_region == 0] = self.eps_r[design_region == 0]
            self.eps_r = eps_full

        elif style == 'halfway':
            # halfway between 1 and eps_m in design region
            eps_halfway = self.eps_r
            eps_halfway[design_region == 1] = eps_m/2 + 1/2
            self.eps_r = eps_halfway

        elif style == 'empty':
            # nothing in design region
            eps_empty = np.ones(self.eps_r.shape)
            eps_empty[design_region == 0] = self.eps_r[design_region == 0]
            self.eps_r = eps_empty

        elif style == 'random':
            # random pixels in design region
            eps_random = (eps_m-1)*np.random.random(self.eps_r.shape)+1
            eps_random[design_region == 0] = self.eps_r[design_region == 0]
            self.eps_r = eps_random

    def plt_re(self, cbar=True, outline=True, ax=None, tiled_y=1):
        """ Plots the real part of primary field (e.g. Ez/Hz)"""

        eps_r = self.eps_r
        eps_r = np.hstack(tiled_y*[eps_r])

        if self.fields[self.pol] is None:
            raise ValueError("need to solve the simulation first")

        field_val = np.real(self.fields[self.pol])
        field_val = np.hstack(tiled_y*[field_val])

        outline_val = np.abs(eps_r)
        vmin = -np.abs(field_val).max()
        vmax = +np.abs(field_val).max()
        cmap = "RdBu"

        return plt_base(field_val, outline_val, cmap, vmin, vmax, self.pol,
                        cbar=cbar, outline=outline, ax=ax)

    def plt_diff(self, cbar=True, outline=True, ax=None, vmax=None, tiled_y=1,
                 normalize=True):
        """ Plots the difference between |E| and |E_nl|"""

        # solve the fields
        (_, _, Ez) = self.solve_fields()
        (_, _, Ez_nl, _) = self.solve_fields_nl()

        # get the outline value
        eps_r = self.eps_r
        eps_r = np.hstack(tiled_y*[eps_r])
        outline_val = np.abs(eps_r)

        # get the fields and tile them
        field_lin = np.abs(Ez)
        field_lin = np.hstack(tiled_y*[field_lin])
        field_nl = np.abs(Ez_nl)
        field_nl = np.hstack(tiled_y*[field_nl])

        # take the difference, normalize by the max E_lin field if desired
        field_diff = field_lin - field_nl
        if normalize:
            field_diff = field_diff/np.abs(field_lin).max()

        # set limits
        if vmax is None:
            vmax = np.abs(field_diff).max()
        vmin = -vmax

        return plt_base(field_diff, outline_val, 'RdYlBu', vmin, vmax,
                        self.pol, cbar=cbar, outline=outline, ax=ax)

    def plt_eps(self, cbar=True, outline=True, ax=None, tiled_y=1):
        # plot the permittivity distribution

        eps_r = self.eps_r
        eps_r = np.hstack(tiled_y*[eps_r])

        eps_val = np.abs(eps_r)
        outline_val = np.abs(eps_r)
        vmin = np.abs(eps_r).min()
        vmax = np.abs(eps_r).max()
        cmap = "Greys"

        return plt_base_eps(eps_val, outline_val, cmap, vmin, vmax, cbar=cbar,
                            outline=outline, ax=ax)
