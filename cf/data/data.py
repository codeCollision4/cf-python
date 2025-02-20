import logging
import math
import operator
from functools import partial, reduce, wraps
from itertools import product
from numbers import Integral
from operator import mul

import cfdm
import cftime
import dask.array as da
import numpy as np
from dask.array import Array
from dask.array.core import normalize_chunks
from dask.base import is_dask_collection, tokenize
from dask.core import flatten
from dask.highlevelgraph import HighLevelGraph

from ..cfdatetime import dt as cf_dt
from ..constants import masked as cf_masked
from ..decorators import (
    _deprecated_kwarg_check,
    _display_or_return,
    _inplace_enabled,
    _inplace_enabled_define_and_cleanup,
    _manage_log_level_via_verbosity,
)
from ..functions import (
    _DEPRECATION_ERROR_KWARGS,
    _section,
    atol,
    default_netCDF_fillvals,
    free_memory,
    log_level,
    parse_indices,
    rtol,
)
from ..mixin_container import Container
from ..units import Units
from .collapse import Collapse
from .creation import compressed_to_dask, generate_axis_identifiers, to_dask
from .dask_utils import (
    _da_ma_allclose,
    cf_contains,
    cf_dt2rt,
    cf_harden_mask,
    cf_percentile,
    cf_rt2dt,
    cf_soften_mask,
    cf_units,
    cf_where,
)
from .mixin import DataClassDeprecationsMixin
from .utils import (  # is_small,; is_very_small,
    YMDhms,
    _is_numeric_dtype,
    conform_units,
    convert_to_datetime,
    convert_to_reftime,
    first_non_missing_value,
    new_axis_identifier,
    scalar_masked_array,
)

_DASKIFIED_VERBOSE = None  # see below for valid levels, adapt as useful


logger = logging.getLogger(__name__)

daskified_log_level = 0


def daskified(apply_temp_log_level=None):
    def decorator(method):
        """Temporary decorator to mark and log methods migrated to Dask.

        A log level argument will set the log level throughout the call of
        the method to that level and then reset it back to the previous
        global level. A message will also be emitted to indicate whenever
        the method is called, unless no argument is given [daskified()]
        in which case the decorator does nothing except mark methods
        which are considered to be daskified, a main purpose for this
        decorator.

        Note: for properties the decorator must be placed underneath the
        property decorator so it is called before and not after it.

        """

        @wraps(method)
        def wrapper(*args, **kwargs):
            if apply_temp_log_level is None:  # distingush from 0
                return method(*args, **kwargs)

            original_global_log_level = log_level()
            # Switch log level for the duration of the method call, with an
            # initial message to indicate a run first guaranteed to show
            log_level(apply_temp_log_level)
            # Not actually a warning, but setting as warning ensures it shows
            # (unless logging is disabled, but ignore that complication for
            # this temporary and informal decorator!)
            logger.warning(f"%%%%% Running daskified {method.__name__} %%%%%")

            out = method(*args, **kwargs)

            # ... then return the log level to the global level afterwards
            log_level(original_global_log_level)
            return out

        return wrapper

    return decorator


# --------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------
_year_length = 365.242198781
_month_length = _year_length / 12

_empty_set = set()

_units_None = Units()
_units_1 = Units("1")
_units_radians = Units("radians")

_dtype_float32 = np.dtype("float32")
_dtype_float = np.dtype(float)
_dtype_bool = np.dtype(bool)

_DEFAULT_CHUNKS = "auto"
_DEFAULT_HARDMASK = True


class Data(DataClassDeprecationsMixin, Container, cfdm.Data):
    """An N-dimensional data array with units and masked values.

    * Contains an N-dimensional, indexable and broadcastable array with
      many similarities to a `numpy` array.

    * Contains the units of the array elements.

    * Supports masked arrays, regardless of whether or not it was
      initialised with a masked array.

    * Stores and operates on data arrays which are larger than the
      available memory.

    **Indexing**

    A data array is indexable in a similar way to numpy array:

    >>> d.shape
    (12, 19, 73, 96)
    >>> d[...].shape
    (12, 19, 73, 96)
    >>> d[slice(0, 9), 10:0:-2, :, :].shape
    (9, 5, 73, 96)

    There are three extensions to the numpy indexing functionality:

    * Size 1 dimensions are never removed by indexing.

      An integer index i takes the i-th element but does not reduce the
      rank of the output array by one:

      >>> d.shape
      (12, 19, 73, 96)
      >>> d[0, ...].shape
      (1, 19, 73, 96)
      >>> d[:, 3, slice(10, 0, -2), 95].shape
      (12, 1, 5, 1)

      Size 1 dimensions may be removed with the `squeeze` method.

    * The indices for each axis work independently.

      When more than one dimension's slice is a 1-d boolean sequence or
      1-d sequence of integers, then these indices work independently
      along each dimension (similar to the way vector subscripts work in
      Fortran), rather than by their elements:

      >>> d.shape
      (12, 19, 73, 96)
      >>> d[0, :, [0, 1], [0, 13, 27]].shape
      (1, 19, 2, 3)

    * Boolean indices may be any object which exposes the numpy array
      interface.

      >>> d.shape
      (12, 19, 73, 96)
      >>> d[..., d[0, 0, 0]>d[0, 0, 0].min()]

    **Cyclic axes**

    """

    def __init__(
        self,
        array=None,
        units=None,
        calendar=None,
        fill_value=None,
        hardmask=_DEFAULT_HARDMASK,
        chunks=_DEFAULT_CHUNKS,
        dt=False,
        source=None,
        copy=True,
        dtype=None,
        mask=None,
        to_memory=False,
        init_options=None,
        _use_array=True,
    ):
        """**Initialization**

        :Parameters:

            array: optional
                The array of values. May be any scalar or array-like
                object, including another `Data` instance.

                *Parameter example:*
                  ``array=[34.6]``

                *Parameter example:*
                  ``array=[[1, 2], [3, 4]]``

                *Parameter example:*
                  ``array=numpy.ma.arange(10).reshape(2, 1, 5)``

            units: `str` or `Units`, optional
                The physical units of the data. if a `Units` object is
                provided then this an also set the calendar.

                The units (without the calendar) may also be set after
                initialisation with the `set_units` method.

                *Parameter example:*
                  ``units='km hr-1'``

                *Parameter example:*
                  ``units='days since 2018-12-01'``

            calendar: `str`, optional
                The calendar for reference time units.

                The calendar may also be set after initialisation with the
                `set_calendar` method.

                *Parameter example:*
                  ``calendar='360_day'``

            fill_value: optional
                The fill value of the data. By default, or if set to
                `None`, the `numpy` fill value appropriate to the array's
                data-type will be used (see
                `numpy.ma.default_fill_value`).

                The fill value may also be set after initialisation with
                the `set_fill_value` method.

                *Parameter example:*
                  ``fill_value=-999.``

            dtype: data-type, optional
                The desired data-type for the data. By default the
                data-type will be inferred form the *array*
                parameter.

                The data-type may also be set after initialisation with
                the `dtype` attribute.

                *Parameter example:*
                    ``dtype=float``

                *Parameter example:*
                    ``dtype='float32'``

                *Parameter example:*
                    ``dtype=numpy.dtype('i2')``

                .. versionadded:: 3.0.4

            mask: optional
                Apply this mask to the data given by the *array*
                parameter. By default, or if *mask* is `None`, no mask
                is applied. May be any scalar or array-like object
                (such as a `list`, `numpy` array or `Data` instance)
                that is broadcastable to the shape of *array*. Masking
                will be carried out where the mask elements evaluate
                to `True`.

                This mask will applied in addition to any mask already
                defined by the *array* parameter.

                .. versionadded:: 3.0.5

            source: optional
                Initialize the data values and metadata (such as
                units, mask hardness, etc.) from the data of
                *source*. All other arguments, with the exception of
                *copy*, are ignored.

            hardmask: `bool`, optional
                If False then the mask is soft. By default the mask is
                hard.

            dt: `bool`, optional
                If True then strings (such as ``'1990-12-01 12:00'``)
                given by the *array* parameter are re-interpreted as
                date-time objects. By default they are not.

            copy: `bool`, optional
                If False then do not deep copy input parameters prior to
                initialization. By default arguments are deep copied.

            {{chunks: `int`, `tuple`, `dict` or `str`, optional}}

                .. versionadded:: TODODASKVER

            to_memory: `bool`, optional
                If True then ensure that the original data are in
                memory, rather than on disk.

                If the original data are on disk, then reading data
                into memory during initialisation will slow down the
                initialisation process, but can considerably improve
                downstream performance by avoiding the need for
                independent reads for every dask chunk, each time the
                data are computed.

                In general, setting *to_memory* to True is not the same
                as calling the `persist` of the newly created `Data`
                object, which also decompresses data compressed by
                convention and computes any data type, mask and
                date-time modifications.

                If the input *array* is a `dask.array.Array` object
                then *to_memory* is ignored.

                .. versionadded:: TODODASKVER

            init_options: `dict`, optional
                Provide optional keyword arguments to methods and
                functions called during the initialisation process. A
                dictionary key identifies a method or function. The
                corresponding value is another dictionary whose
                key/value pairs are the keyword parameter names and
                values to be applied.

                Supported keys are:

                * ``'from_array'``: Provide keyword arguments to
                  the `dask.array.from_array` function. This is used
                  when initialising data that is not already a dask
                  array and is not compressed by convention.

                * ``'first_non_missing_value'``: Provide keyword
                  arguments to the
                  `cf.data.utils.first_non_missing_value`
                  function. This is used when the input array contains
                  date-time strings or objects, and may affect
                  performance.

                 *Parameter example:*
                   ``{'from_array': {'inline_array': True}}``

            chunk: deprecated at version TODODASKVER
                Use the *chunks* parameter instead.

        **Examples**

        >>> d = cf.Data(5)
        >>> d = cf.Data([1,2,3], units='K')
        >>> import numpy
        >>> d = cf.Data(numpy.arange(10).reshape(2,5),
        ...             units=Units('m/s'), fill_value=-999)
        >>> d = cf.Data('fly')
        >>> d = cf.Data(tuple('fly'))

        """
        if source is None and isinstance(array, self.__class__):
            source = array

        if init_options is None:
            init_options = {}

        if source is not None:
            try:
                array = source._get_Array(None)
            except AttributeError:
                array = None

            super().__init__(
                source=source, _use_array=_use_array and array is not None
            )

            if _use_array:
                try:
                    array = source.to_dask_array()
                except (AttributeError, TypeError):
                    pass
                else:
                    self._set_dask(array, copy=copy, delete_source=False)
            else:
                self._del_dask(None)

            # Set the mask hardness
            self.hardmask = getattr(source, "hardmask", _DEFAULT_HARDMASK)

            return

        super().__init__(
            array=array,
            fill_value=fill_value,
            _use_array=False,
        )

        # Set the units
        units = Units(units, calendar=calendar)
        self._Units = units

        # Set the mask hardness
        self.hardmask = hardmask

        if array is None:
            return

        try:
            ndim = array.ndim
        except AttributeError:
            ndim = np.ndim(array)

        # Create the _cyclic attribute: identifies which axes are
        # cyclic (and therefore allow cyclic slicing). It must be a
        # subset of the axes given by the _axes attribute. If an axis
        # is removed from _axes then it must also be removed from
        # _cyclic.
        #
        # Never change the value of the _cyclic attribute in-place.
        self._cyclic = _empty_set

        # Create the _axes attribute: an ordered sequence of unique
        # (within this `Data` instance) names for each array axis.
        self._axes = generate_axis_identifiers(ndim)

        if not _use_array:
            return

        # Still here? Then create a dask array and store it.

        # Find out if the data is compressed
        try:
            compressed = array.get_compression_type()
        except AttributeError:
            compressed = ""

        if compressed:
            # The data is compressed, so create a uncompressed dask
            # view of it.
            if chunks != _DEFAULT_CHUNKS:
                raise ValueError(
                    "Can't define chunks for compressed input arrays. "
                    "Consider rechunking after initialisation."
                )

            if init_options.get("from_array"):
                raise ValueError(
                    "Can't define 'from_array' initialisation options "
                    "for compressed input arrays"
                )

            # Bring the compressed data into memory without
            # decompressing it
            if to_memory:
                try:
                    array = array.to_memory()
                except AttributeError:
                    pass

            # Save the input compressed array, as this will contain
            # extra information, such as a count or index variable.
            self._set_Array(array)

            array = compressed_to_dask(array, chunks)

        elif not is_dask_collection(array):
            # Turn the data into a dask array
            kwargs = init_options.get("from_array", {})
            if "chunks" in kwargs:
                raise TypeError(
                    "Can't define 'chunks' in the 'from_array' "
                    "initialisation options. "
                    "Use the 'chunks' parameter instead."
                )

            # Bring the data into memory
            if to_memory:
                try:
                    array = array.to_memory()
                except AttributeError:
                    pass

            array = to_dask(array, chunks, **kwargs)

        elif chunks != _DEFAULT_CHUNKS:
            # The data is already a dask array
            raise ValueError(
                "Can't define chunks for dask input arrays. Consider "
                "rechunking the dask array before initialisation, or "
                "rechunking the Data after initialisation."
            )

        # Find out if we have an array of date-time objects
        if units.isreftime:
            dt = True

        first_value = None
        if not dt and array.dtype.kind == "O":
            kwargs = init_options.get("first_non_missing_value", {})
            first_value = first_non_missing_value(array, **kwargs)

            if first_value is not None:
                dt = hasattr(first_value, "timetuple")

        # Convert string or object date-times to floating point
        # reference times
        if dt and array.dtype.kind in "USO":
            array, units = convert_to_reftime(array, units, first_value)
            # Reset the units
            self._Units = units

        # Store the dask array
        self._set_dask(array, delete_source=False)

        # Override the data type
        if dtype is not None:
            self.dtype = dtype

        # Apply a mask
        if mask is not None:
            self.where(mask, cf_masked, inplace=True)

    @property
    def dask_compressed_array(self):
        """TODODASKDOCS.

        :Returns:

            `dask.array.Array`

        """
        ca = self.source(None)

        if ca is None or not ca.get_compression_type():
            raise ValueError("not compressed: can't get compressed dask array")

        return ca.to_dask_array()

    @daskified(_DASKIFIED_VERBOSE)
    def __contains__(self, value):
        """Membership test operator ``in``

        x.__contains__(y) <==> y in x

        Returns True if the scalar *value* is contained anywhere in
        the data. If *value* is not scalar then an exception is
        raised.

        **Performance**

        `__contains__` causes all delayed operations to be computed
        unless *value* is a `Data` object with incompatible units, in
        which case `False` is always returned.

        **Examples**

        >>> d = cf.Data([[0, 1, 2], [3, 4, 5]], 'm')
        >>> 4 in d
        True
        >>> 4.0 in d
        True
        >>> cf.Data(5) in d
        True
        >>> cf.Data(5, 'm') in d
        True
        >>> cf.Data(0.005, 'km') in d
        True

        >>> 99 in d
        False
        >>> cf.Data(2, 'seconds') in d
        False

        >>> [1] in d
        Traceback (most recent call last):
            ...
        TypeError: elementwise comparison failed; must test against a scalar, not [1]
        >>> [1, 2] in d
        Traceback (most recent call last):
            ...
        TypeError: elementwise comparison failed; must test against a scalar, not [1, 2]

        >>> d = cf.Data(["foo", "bar"])
        >>> 'foo' in d
        True
        >>> 'xyz' in d
        False

        """
        # Check that value is scalar by seeing if its shape is ()
        shape = getattr(value, "shape", None)
        if shape is None:
            if isinstance(value, str):
                # Strings are scalars, even though they have a len().
                shape = ()
            else:
                try:
                    len(value)
                except TypeError:
                    # value has no len() so assume that it is a scalar
                    shape = ()
                else:
                    # value has a len() so assume that it is not a scalar
                    shape = True
        elif is_dask_collection(value) and math.isnan(value.size):
            # value is a dask array with unknown size, so calculate
            # the size. This is acceptable, as we're going to compute
            # it anyway at the end of this method.
            value.compute_chunk_sizes()
            shape = value.shape

        if shape:
            raise TypeError(
                "elementwise comparison failed; must test against a scalar, "
                f"not {value!r}"
            )

        # If value is a scalar Data object then conform its units
        if isinstance(value, self.__class__):
            self_units = self.Units
            value_units = value.Units
            if value_units.equivalent(self_units):
                if not value_units.equals(self_units):
                    value = value.copy()
                    value.Units = self_units
            elif value_units:
                # No need to check the dask array if the value units
                # are incompatible
                return False

            value = value.to_dask_array()

        dx = self.to_dask_array()

        out_ind = tuple(range(dx.ndim))
        dx_ind = out_ind

        dx = da.blockwise(
            cf_contains,
            out_ind,
            dx,
            dx_ind,
            value,
            (),
            adjust_chunks={i: 1 for i in out_ind},
            dtype=bool,
        )

        return bool(dx.any())

    @property
    @daskified(_DASKIFIED_VERBOSE)
    def _atol(self):
        """Return the current value of the `cf.atol` function."""
        return atol().value

    @property
    @daskified(_DASKIFIED_VERBOSE)
    def _rtol(self):
        """Return the current value of the `cf.rtol` function."""
        return rtol().value

    def _is_abstract_Array_subclass(self, array):
        """Whether or not an array is a type of abstract Array.

        :Parameters:

            array:

        :Returns:

            `bool`

        """
        return isinstance(array, cfdm.Array)

    @daskified(_DASKIFIED_VERBOSE)
    def __data__(self):
        """Returns a new reference to self."""
        return self

    @daskified(_DASKIFIED_VERBOSE)
    def __float__(self):
        """Called to implement the built-in function `float`

        x.__float__() <==> float(x)

        **Performance**

        `__float__` causes all delayed operations to be executed,
        unless the dask array size is already known to be greater than
        1.

        """
        return float(self.to_dask_array())

    @daskified(_DASKIFIED_VERBOSE)
    def __int__(self):
        """Called to implement the built-in function `int`

        x.__int__() <==> int(x)

        **Performance**

        `__int__` causes all delayed operations to be executed, unless
        the dask array size is already known to be greater than 1.

        """
        return int(self.to_dask_array())

    def __iter__(self):
        """Called when an iterator is required.

        x.__iter__() <==> iter(x)

        **Performance**

        If the shape of the data is unknown then it is calculated
        immediately by executing all delayed operations.

        **Examples**

        >>> d = cf.Data([1, 2, 3], 'metres')
        >>> for e in d:
        ...     print(repr(e))
        ...
        <CF Data(1): [1] metres>
        <CF Data(1): [2] metres>
        <CF Data(1): [3] metres>

        >>> d = cf.Data([[1, 2], [3, 4]], 'metres')
        >>> for e in d:
        ...     print(repr(e))
        ...
        <CF Data: [1, 2] metres>
        <CF Data: [3, 4] metres>

        >>> d = cf.Data(99, 'metres')
        >>> for e in d:
        ...     print(repr(e))
        ...
        Traceback (most recent call last):
            ...
        TypeError: iteration over a 0-d Data

        """
        try:
            n = len(self)
        except TypeError:
            raise TypeError(f"iteration over a 0-d {self.__class__.__name__}")

        for i in range(n):
            yield self[i]

    def __len__(self):
        """Called to implement the built-in function `len`.

        x.__len__() <==> len(x)

        **Performance**

        If the shape of the data is unknown then it is calculated
        immediately by executing all delayed operations.

        **Examples**

        >>> len(cf.Data([1, 2, 3]))
        3
        >>> len(cf.Data([[1, 2, 3]]))
        1
        >>> len(cf.Data([[1, 2, 3], [4, 5, 6]]))
        2
        >>> len(cf.Data(1))
        Traceback (most recent call last):
            ...
        TypeError: len() of unsized object

        """
        dx = self.to_dask_array()
        if math.isnan(dx.size):
            logger.debug("Computing data len: Performance may be degraded")
            dx.compute_chunk_sizes()

        return len(dx)

    def __bool__(self):
        """Truth value testing and the built-in operation `bool`

        x.__bool__() <==> bool(x)

        **Performance**

        `__bool__` causes all delayed operations to be computed.

        **Examples**

        >>> bool(cf.Data(1.5))
        True
        >>> bool(cf.Data([[False]]))
        False

        """
        size = self.size
        if size != 1:
            raise ValueError(
                f"The truth value of a {self.__class__.__name__} with {size} "
                "elements is ambiguous. Use d.any() or d.all()"
            )

        return bool(self.to_dask_array())

    def __repr__(self):
        """Called by the `repr` built-in function.

        x.__repr__() <==> repr(x)

        """
        return super().__repr__().replace("<", "<CF ", 1)

    @daskified(_DASKIFIED_VERBOSE)
    def __getitem__(self, indices):
        """Return a subspace of the data defined by indices.

        d.__getitem__(indices) <==> d[indices]

        Indexing follows rules that are very similar to the numpy indexing
        rules, the only differences being:

        * An integer index i takes the i-th element but does not reduce
          the rank by one.

        * When two or more dimensions' indices are sequences of integers
          then these indices work independently along each dimension
          (similar to the way vector subscripts work in Fortran). This is
          the same behaviour as indexing on a `netCDF4.Variable` object.

        **Performance**

        If the shape of the data is unknown then it is calculated
        immediately by exectuting all delayed operations.

        . seealso:: `__setitem__`, `__keepdims_indexing__`,
                    `__orthogonal_indexing__`

        :Returns:

            `Data`
                The subspace of the data.

        **Examples**

        >>> import numpy
        >>> d = Data(numpy.arange(100, 190).reshape(1, 10, 9))
        >>> d.shape
        (1, 10, 9)
        >>> d[:, :, 1].shape
        (1, 10, 1)
        >>> d[:, 0].shape
        (1, 1, 9)
        >>> d[..., 6:3:-1, 3:6].shape
        (1, 3, 3)
        >>> d[0, [2, 9], [4, 8]].shape
        (1, 2, 2)
        >>> d[0, :, -2].shape
        (1, 10, 1)

        """
        if indices is Ellipsis:
            return self.copy()

        ancillary_mask = ()
        try:
            arg = indices[0]
        except (IndexError, TypeError):
            pass
        else:
            if isinstance(arg, str) and arg == "mask":
                ancillary_mask = indices[1]
                indices = indices[2:]

        shape = self.shape
        keepdims = self.__keepdims_indexing__

        indices, roll = parse_indices(
            shape, indices, cyclic=True, keepdims=keepdims
        )

        axes = self._axes
        cyclic_axes = self._cyclic

        # ------------------------------------------------------------
        # Roll axes with cyclic slices
        # ------------------------------------------------------------
        if roll:
            # For example, if slice(-2, 3) has been requested on a
            # cyclic axis, then we roll that axis by two points and
            # apply the slice(0, 5) instead.
            if not cyclic_axes.issuperset([axes[i] for i in roll]):
                raise IndexError(
                    "Can't take a cyclic slice of a non-cyclic axis"
                )

            new = self.roll(
                axis=tuple(roll.keys()), shift=tuple(roll.values())
            )
            dx = new.to_dask_array()
        else:
            new = self.copy(array=False)
            dx = self.to_dask_array()

        # ------------------------------------------------------------
        # Subspace the dask array
        # ------------------------------------------------------------
        if self.__orthogonal_indexing__:
            # Apply 'orthogonal indexing': indices that are 1-d arrays
            # or lists subspace along each dimension
            # independently. This behaviour is similar to Fortran, but
            # different to dask.
            axes_with_list_indices = [
                i
                for i, x in enumerate(indices)
                if isinstance(x, list) or getattr(x, "shape", False)
            ]
            n_axes_with_list_indices = len(axes_with_list_indices)

            if n_axes_with_list_indices < 2:
                # At most one axis has a list/1-d array index so do a
                # normal dask subspace
                dx = dx[tuple(indices)]
            else:
                # At least two axes have list/1-d array indices so we
                # can't do a normal dask subspace

                # Subspace axes which have list/1-d array indices
                for axis in axes_with_list_indices:
                    dx = da.take(dx, indices[axis], axis=axis)

                if n_axes_with_list_indices < len(indices):
                    # Subspace axes which don't have list/1-d array
                    # indices. (Do this after subspacing axes which do
                    # have list/1-d array indices, in case
                    # __keepdims_indexing__ is False.)
                    slice_indices = [
                        slice(None) if i in axes_with_list_indices else x
                        for i, x in enumerate(indices)
                    ]
                    dx = dx[tuple(slice_indices)]
        else:
            raise NotImplementedError(
                "Non-orthogonal indexing has not yet been implemented"
            )

        # ------------------------------------------------------------
        # Set the subspaced dask array
        # ------------------------------------------------------------
        new._set_dask(dx)

        # ------------------------------------------------------------
        # Get the axis identifiers for the subspace
        # ------------------------------------------------------------
        shape0 = shape
        if keepdims:
            new_axes = axes
        else:
            new_axes = [
                axis
                for axis, x in zip(axes, indices)
                if not isinstance(x, Integral) and getattr(x, "shape", True)
            ]
            if new_axes != axes:
                new._axes = new_axes
                cyclic_axes = new._cyclic
                if cyclic_axes:
                    shape0 = [
                        n for n, axis in zip(shape, axes) if axis in new_axes
                    ]

        # ------------------------------------------------------------
        # Cyclic axes that have been reduced in size are no longer
        # considered to be cyclic
        # ------------------------------------------------------------
        if cyclic_axes:
            x = [
                axis
                for axis, n0, n1 in zip(new_axes, shape0, new.shape)
                if axis in cyclic_axes and n0 != n1
            ]
            if x:
                # Never change the value of the _cyclic attribute
                # in-place
                new._cyclic = cyclic_axes.difference(x)

        # ------------------------------------------------------------
        # Apply ancillary masks
        # ------------------------------------------------------------
        for mask in ancillary_mask:
            new.where(mask, cf_masked, None, inplace=True)

        if new.shape != self.shape:
            # Delete hdf5 chunksizes when the shape has changed.
            new.nc_clear_hdf5_chunksizes()

        return new

    @daskified(_DASKIFIED_VERBOSE)
    def __setitem__(self, indices, value):
        """Implement indexed assignment.

        x.__setitem__(indices, y) <==> x[indices]=y

        Assignment to data array elements defined by indices.

        Elements of a data array may be changed by assigning values to
        a subspace. See `__getitem__` for details on how to define
        subspace of the data array.

        .. note:: Currently at most one dimension's assignment index
                  may be a 1-d array of integers or booleans. This is
                  is different to `__getitem__`, which by default
                  applies 'orthogonal indexing' when multiple indices
                  of 1-d array of integers or booleans are present.

        **Missing data**

        The treatment of missing data elements during assignment to a
        subspace depends on the value of the `hardmask` attribute. If
        it is True then masked elements will not be unmasked,
        otherwise masked elements may be set to any value.

        In either case, unmasked elements may be set, (including
        missing data).

        Unmasked elements may be set to missing data by assignment to
        the `cf.masked` constant or by assignment to a value which
        contains masked elements.

        **Performance**

        If the shape of the data is unknown then it is calculated
        immediately by executing all delayed operations.

        If indices for two or more dimensions are lists or 1-d arrays
        of Booleans or integers, and any of these are dask
        collections, then these dask collections will be
        computed immediately.

        .. seealso:: `__getitem__`, `__keedims_indexing__`,
                     `__orthogonal_indexing__`, `cf.masked`,
                     `hardmask`, `where`

        """
        shape = self.shape

        indices, roll = parse_indices(
            shape,
            indices,
            cyclic=True,
            keepdims=self.__keepdims_indexing__,
        )

        axes_with_list_indices = [
            i
            for i, x in enumerate(indices)
            if isinstance(x, list) or getattr(x, "shape", False)
        ]

        # When there are two or more 1-d array indices of Booleans or
        # integers, convert them to slices, if possible.
        #
        # Note: If any of these 1-d arrays is a dask collection, then
        #       this will be computed.
        if len(axes_with_list_indices) > 1:
            for i, index in enumerate(indices):
                if not (
                    isinstance(index, list) or getattr(index, "shape", False)
                ):
                    # Not a 1-d array
                    continue

                index = np.array(index)

                size = shape[i]
                if index.dtype == bool:
                    # Convert True values to integers
                    index = np.arange(size)[index]
                else:
                    # Make sure all integer values are non-negative
                    index = np.where(index < 0, index + size, index)

                if size == 1:
                    start = index[0]
                    index = slice(start, start + 1)
                else:
                    steps = index[1:] - index[:-1]
                    step = steps[0]
                    if step and not (steps - step).any():
                        # Array has a regular step, and so can be
                        # converted to a slice.
                        if step > 0:
                            start, stop = index[0], index[-1] + 1
                        elif step < 0:
                            start, stop = index[0], index[-1] - 1

                        if stop < 0:
                            stop = None

                        index = slice(start, stop, step)

                indices[i] = index

        # Roll axes with cyclic slices
        if roll:
            # For example, if assigning to slice(-2, 3) has been
            # requested on a cyclic axis (and we're not using numpy
            # indexing), then we roll that axis by two points and
            # assign to slice(0, 5) instead. The axis is then unrolled
            # by two points afer the assignment has been made.
            axes = self._axes
            if not self._cyclic.issuperset([axes[i] for i in roll]):
                raise IndexError(
                    "Can't do a cyclic assignment to a non-cyclic axis"
                )

            roll_axes = tuple(roll.keys())
            shifts = tuple(roll.values())
            self.roll(shift=shifts, axis=roll_axes, inplace=True)

        # Make sure that the units of value are the same as self
        value = conform_units(value, self.Units)

        # Missing values could be affected, so make sure that the mask
        # hardness has been applied.
        dx = self.to_dask_array(apply_mask_hardness=True)

        # Do the assignment
        self._set_subspace(dx, indices, value)

        # Unroll any axes that were rolled to enable a cyclic
        # assignment
        if roll:
            shifts = [-shift for shift in shifts]
            self.roll(shift=shifts, axis=roll_axes, inplace=True)

        # Remove a source array, on the grounds that we can't
        # guarantee its consistency with the updated dask array.
        self._del_Array(None)

        return

    # ----------------------------------------------------------------
    # Indexing behaviour attributes
    # ----------------------------------------------------------------
    @property
    @daskified(_DASKIFIED_VERBOSE)
    def __orthogonal_indexing__(self):
        """Flag to indicate that orthogonal indexing is supported.

        Always True, indicating that 'orthogonal indexing' is
        applied. This means that when indices are 1-d arrays or lists
        then they subspace along each dimension independently. This
        behaviour is similar to Fortran, but different to `numpy`.

        .. versionadded:: TODODASKVER

        .. seealso:: `__keepdims_indexing__`, `__getitem__`,
                     `__setitem__`,
                     `netCDF4.Variable.__orthogonal_indexing__`

        **Examples**

        >>> d = cf.Data([[1, 2, 3],
        ...              [4, 5, 6]])
        >>> e = d[[0], [0, 2]]
        >>> e.shape
        (1, 2)
        >>> print(e.array)
        [[1 3]]
        >>> e = d[[0, 1], [0, 2]]
        >>> e.shape
        (2, 2)
        >>> print(e.array)
        [[1 3]
         [4 6]]

        """
        return True

    @property
    @daskified(_DASKIFIED_VERBOSE)
    def __keepdims_indexing__(self):
        """Flag to indicate whether dimensions indexed with integers are
        kept.

        If set to True (the default) then providing a single integer
        as a single-axis index does *not* reduce the number of array
        dimensions by 1. This behaviour is different to `numpy`.

        If set to False then providing a single integer as a
        single-axis index reduces the number of array dimensions by
        1. This behaviour is the same as `numpy`.

        .. versionadded:: TODODASKVER

        .. seealso:: `__orthogonal_indexing__`, `__getitem__`,
                     `__setitem__`

        **Examples**

        >>> d = cf.Data([[1, 2, 3],
        ...              [4, 5, 6]])
        >>> d.__keepdims_indexing__
        True
        >>> e = d[0]
        >>> e.shape
        (1, 3)
        >>> print(e.array)
        [[1 2 3]]

        >>> d.__keepdims_indexing__
        True
        >>> e = d[:, 1]
        >>> e.shape
        (2, 1)
        >>> print(e.array)
        [[2]
         [5]]

        >>> d.__keepdims_indexing__
        True
        >>> e = d[0, 1]
        >>> e.shape
        (1, 1)
        >>> print(e.array)
        [[2]]

        >>> d.__keepdims_indexing__ = False
        >>> e = d[0]
        >>> e.shape
        (3,)
        >>> print(e.array)
        [1 2 3]

        >>> d.__keepdims_indexing__
        False
        >>> e = d[:, 1]
        >>> e.shape
        (2,)
        >>> print(e.array)
        [2 5]

        >>> d.__keepdims_indexing__
        False
        >>> e = d[0, 1]
        >>> e.shape
        ()
        >>> print(e.array)
        2

        """
        return self._custom.get("__keepdims_indexing__", True)

    @__keepdims_indexing__.setter
    def __keepdims_indexing__(self, value):
        self._custom["__keepdims_indexing__"] = bool(value)

    def _set_dask(self, array, copy=False, delete_source=True):
        """Set the dask array.

        .. versionadded:: TODODASKVER

        .. seealso:: `to_dask_array`, `_del_dask`

        :Parameters:

            array: `dask.array.Array`
                The array to be inserted.

            copy: `bool`, optional
                If True then copy *array* before setting it. By
                default it is not copied.

            delete_source: `bool`, optional
                If False then do not delete a source array, if one
                exists, after setting the new dask array. By default a
                source array is deleted.

        :Returns:

            `None`

        """
        if array is NotImplemented:
            logger.warning(
                "NotImplemented has been set in the place of a dask array"
            )
            # This could occur if any sort of exception is raised by
            # function that is run on chunks (such as
            # `cf_where`). Such a function could get run at definition
            # time in order to ascertain suitability (such as data
            # type casting, broadcasting, etc.). Note that the
            # exception may be difficult to diagnose, as dask will
            # have silently trapped it and returned NotImplemented
            # (for instance, see `dask.array.core.elemwise`). Print
            # statements in a local copy of dask are prossibly the way
            # to go if the cause of the error is not obvious.

        if copy:
            array = array.copy()

        self._custom["dask"] = array

        if delete_source:
            # Remove a source array, on the grounds that we can't
            # guarantee its consistency with the new dask array.
            self._del_Array(None)

    def _del_dask(self, default=ValueError(), delete_source=True):
        """Remove the dask array.

        .. versionadded:: TODODASKVER

        .. seealso:: `_set_dask`, `to_dask_array`

        :Parameters:

            default: optional
                Return the value of the *default* parameter if the
                dask array axes has not been set.

                {{default Exception}}

            delete_source: `bool`, optional
                If False then do not delete a compressed source array,
                if one exists.

        :Returns:

            `dask.array.Array`
                The removed dask array.

        **Examples**

        >>> d = cf.Data([1, 2, 3])
        >>> dx = d._del_dask()
        >>> d._del_dask("No dask array")
        'No dask array'
        >>> d._del_dask()
        Traceback (most recent call last):
            ...
        ValueError: 'Data' has no dask array
        >>> d._del_dask(RuntimeError('No dask array'))
        Traceback (most recent call last):
            ...
        RuntimeError: No dask array

        """
        try:
            out = self._custom.pop("dask")
        except KeyError:
            return self._default(
                default, f"{self.__class__.__name__!r} has no dask array"
            )

        if delete_source:
            # Remove a source array, on the grounds that we can't
            # guarantee its consistency with any future new dask
            # array.
            self._del_Array(None)

        return out

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def diff(self, axis=-1, n=1, inplace=False):
        """Calculate the n-th discrete difference along the given axis.

        The first difference is given by ``x[i+1] - x[i]`` along the
        given axis, higher differences are calculated by using `diff`
        recursively.

        The shape of the output is the same as the input except along
        the given axis, where the dimension is smaller by *n*. The
        data type of the output is the same as the type of the
        difference between any two elements of the input.

        .. versionadded:: 3.2.0

        .. seealso:: `cumsum`, `sum`

        :Parameters:

            axis: int, optional
                The axis along which the difference is taken. By
                default the last axis is used. The *axis* argument is
                an integer that selects the axis corresponding to the
                given position in the list of axes of the data array.

            n: int, optional
                The number of times values are differenced. If zero,
                the input is returned as-is. By default *n* is ``1``.

            {{inplace: `bool`, optional}}

        :Returns:

            `Data` or `None`
                The n-th differences, or `None` if the operation was
                in-place.

        **Examples**

        >>> d = cf.Data(numpy.arange(12.).reshape(3, 4))
        >>> d[1, 1] = 4.5
        >>> d[2, 2] = 10.5
        >>> print(d.array)
        [[ 0.   1.   2.   3. ]
         [ 4.   4.5  6.   7. ]
         [ 8.   9.  10.5 11. ]]
        >>> print(d.diff().array)
        [[1.  1.  1. ]
         [0.5 1.5 1. ]
         [1.  1.5 0.5]]
        >>> print(d.diff(n=2).array)
        [[ 0.   0. ]
         [ 1.  -0.5]
         [ 0.5 -1. ]]
        >>> print(d.diff(axis=0).array)
        [[4.  3.5 4.  4. ]
         [4.  4.5 4.5 4. ]]
        >>> print(d.diff(axis=0, n=2).array)
        [[0.  1.  0.5 0. ]]
        >>> d[1, 2] = cf.masked
        >>> print(d.array)
        [[0.0 1.0  2.0  3.0]
         [4.0 4.5   --  7.0]
         [8.0 9.0 10.5 11.0]]
        >>> print(d.diff().array)
        [[1.0 1.0 1.0]
         [0.5  --  --]
         [1.0 1.5 0.5]]
        >>> print(d.diff(n=2).array)
        [[0.0  0.0]
         [ --   --]
         [0.5 -1.0]]
        >>> print(d.diff(axis=0).array)
        [[4.0 3.5 -- 4.0]
         [4.0 4.5 -- 4.0]]
        >>> print(d.diff(axis=0, n=2).array)
        [[0.0 1.0 -- 0.0]]

        """
        d = _inplace_enabled_define_and_cleanup(self)

        dx = self.to_dask_array()
        dx = da.diff(dx, axis=axis, n=n)
        d._set_dask(dx)

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def digitize(
        self,
        bins,
        upper=False,
        open_ends=False,
        closed_ends=None,
        return_bins=False,
        inplace=False,
    ):
        """Return the indices of the bins to which each value belongs.

        Values (including masked values) that do not belong to any bin
        result in masked values in the output data.

        Bins defined by percentiles are easily created with the
        `percentiles` method

        *Example*:
          Find the indices for bins defined by the 10th, 50th and 90th
          percentiles:

          >>> bins = d.percentile([0, 10, 50, 90, 100], squeeze=True)
          >>> i = f.digitize(bins, closed_ends=True)

        .. versionadded:: 3.0.2

        .. seealso:: `percentile`

        :Parameters:

            bins: array_like
                The bin boundaries. One of:

                * An integer.

                  Create this many equally sized, contiguous bins spanning
                  the range of the data. I.e. the smallest bin boundary is
                  the minimum of the data and the largest bin boundary is
                  the maximum of the data. In order to guarantee that each
                  data value lies inside a bin, the *closed_ends*
                  parameter is assumed to be True.

                * A 1-d array of numbers.

                  When sorted into a monotonically increasing sequence,
                  each boundary, with the exception of the two end
                  boundaries, counts as the upper boundary of one bin and
                  the lower boundary of next. If the *open_ends* parameter
                  is True then the lowest lower bin boundary also defines
                  a left-open (i.e. not bounded below) bin, and the
                  largest upper bin boundary also defines a right-open
                  (i.e. not bounded above) bin.

                * A 2-d array of numbers.

                  The second dimension, that must have size 2, contains
                  the lower and upper bin boundaries. Different bins may
                  share a boundary, but may not overlap. If the
                  *open_ends* parameter is True then the lowest lower bin
                  boundary also defines a left-open (i.e. not bounded
                  below) bin, and the largest upper bin boundary also
                  defines a right-open (i.e. not bounded above) bin.

            upper: `bool`, optional
                If True then each bin includes its upper bound but not its
                lower bound. By default the opposite is applied, i.e. each
                bin includes its lower bound but not its upper bound.

            open_ends: `bool`, optional
                If True then create left-open (i.e. not bounded below) and
                right-open (i.e. not bounded above) bins from the lowest
                lower bin boundary and largest upper bin boundary
                respectively. By default these bins are not created

            closed_ends: `bool`, optional
                If True then extend the most extreme open boundary by a
                small amount so that its bin includes values that are
                equal to the unadjusted boundary value. This is done by
                multiplying it by ``1.0 - epsilon`` or ``1.0 + epsilon``,
                whichever extends the boundary in the appropriate
                direction, where ``epsilon`` is the smallest positive
                64-bit float such that ``1.0 + epsilson != 1.0``. I.e. if
                *upper* is False then the largest upper bin boundary is
                made slightly larger and if *upper* is True then the
                lowest lower bin boundary is made slightly lower.

                By default *closed_ends* is assumed to be True if *bins*
                is a scalar and False otherwise.

            return_bins: `bool`, optional
                If True then also return the bins in their 2-d form.

            {{inplace: `bool`, optional}}

        :Returns:

            `Data`, [`Data`]
                The indices of the bins to which each value belongs.

                If *return_bins* is True then also return the bins in
                their 2-d form.

        **Examples**

        >>> d = cf.Data(numpy.arange(12).reshape(3, 4))
        [[ 0  1  2  3]
         [ 4  5  6  7]
         [ 8  9 10 11]]

        Equivalant ways to create indices for the four bins ``[-inf, 2),
        [2, 6), [6, 10), [10, inf)``

        >>> e = d.digitize([2, 6, 10])
        >>> e = d.digitize([[2, 6], [6, 10]])
        >>> print(e.array)
        [[0 0 1 1]
         [1 1 2 2]
         [2 2 3 3]]

        Equivalant ways to create indices for the two bins ``(2, 6], (6, 10]``

        >>> e = d.digitize([2, 6, 10], upper=True, open_ends=False)
        >>> e = d.digitize([[2, 6], [6, 10]], upper=True, open_ends=False)
        >>> print(e.array)
        [[-- -- --  0]
         [ 0  0  0  1]
         [ 1  1  1 --]]

        Create indices for the two bins ``[2, 6), [8, 10)``, which are
        non-contiguous

        >>> e = d.digitize([[2, 6], [8, 10]])
        >>> print(e.array)
        [[ 0 0  1  1]
         [ 1 1 -- --]
         [ 2 2  3  3]]

        Masked values result in masked indices in the output array.

        >>> d[1, 1] = cf.masked
        >>> print(d.array)
        [[ 0  1  2  3]
         [ 4 --  6  7]
         [ 8  9 10 11]]
        >>> print(d.digitize([2, 6, 10], open_ends=True).array)
        [[ 0  0  1  1]
         [ 1 --  2  2]
         [ 2  2  3  3]]
        >>> print(d.digitize([2, 6, 10]).array)
        [[-- --  0  0]
         [ 0 --  1  1]
         [ 1  1 -- --]]
        >>> print(d.digitize([2, 6, 10], closed_ends=True).array)
        [[-- --  0  0]
         [ 0 --  1  1]
         [ 1  1  1 --]]

        """
        d = _inplace_enabled_define_and_cleanup(self)

        org_units = d.Units

        bin_units = getattr(bins, "Units", None)

        if bin_units:
            if not bin_units.equivalent(org_units):
                raise ValueError(
                    "Can't put data into bins that have units that are "
                    "not equivalent to the units of the data."
                )

            if not bin_units.equals(org_units):
                bins = bins.copy()
                bins.Units = org_units
        else:
            bin_units = org_units

        # Get bins as a numpy array
        if isinstance(bins, np.ndarray):
            bins = bins.copy()
        else:
            bins = np.asanyarray(bins)

        if bins.ndim > 2:
            raise ValueError(
                "The 'bins' parameter must be scalar, 1-d or 2-d. "
                f"Got: {bins!r}"
            )

        two_d_bins = None

        if bins.ndim == 2:
            # --------------------------------------------------------
            # 2-d bins: Make sure that each bin is increasing and sort
            #           the bins by lower bounds
            # --------------------------------------------------------
            if bins.shape[1] != 2:
                raise ValueError(
                    "The second dimension of the 'bins' parameter must "
                    f"have size 2. Got: {bins!r}"
                )

            bins.sort(axis=1)
            bins.sort(axis=0)

            # Check for overlaps
            for i, (u, l) in enumerate(zip(bins[:-1, 1], bins[1:, 0])):
                if u > l:
                    raise ValueError(
                        f"Overlapping bins: "
                        f"{tuple(bins[i])}, {tuple(bins[i + i])}"
                    )

            two_d_bins = bins
            bins = np.unique(bins)

            # Find the bins that were omitted from the original 2-d
            # bins array. Note that this includes the left-open and
            # right-open bins at the ends.
            delete_bins = [
                n + 1
                for n, (a, b) in enumerate(zip(bins[:-1], bins[1:]))
                if (a, b) not in two_d_bins
            ]
        elif bins.ndim == 1:
            # --------------------------------------------------------
            # 1-d bins:
            # --------------------------------------------------------
            bins.sort()
            delete_bins = []
        else:
            # --------------------------------------------------------
            # 0-d bins:
            # --------------------------------------------------------
            if closed_ends is None:
                closed_ends = True

            if not closed_ends:
                raise ValueError(
                    "Can't set closed_ends=False when specifying bins as "
                    "a scalar."
                )

            if open_ends:
                raise ValueError(
                    "Can't set open_ends=True when specifying bins as a "
                    "scalar."
                )

            mx = d.max().datum()
            mn = d.min().datum()
            bins = np.linspace(mn, mx, int(bins) + 1, dtype=float)

            delete_bins = []

        if closed_ends:
            # Adjust the lowest/largest bin boundary to be inclusive
            if open_ends:
                raise ValueError(
                    "Can't set open_ends=True when closed_ends is True."
                )

            if bins.dtype.kind != "f":
                bins = bins.astype(float, copy=False)

            epsilon = np.finfo(float).eps
            ndim = bins.ndim
            if upper:
                mn = bins[(0,) * ndim]
                bins[(0,) * ndim] -= abs(mn) * epsilon
            else:
                mx = bins[(-1,) * ndim]
                bins[(-1,) * ndim] += abs(mx) * epsilon

        if not open_ends:
            delete_bins.insert(0, 0)
            delete_bins.append(bins.size)

        # Digitise the array
        dx = d.to_dask_array()
        dx = da.digitize(dx, bins, right=upper)
        d._set_dask(dx)
        d.override_units(_units_None, inplace=True)

        if return_bins:
            if two_d_bins is None:
                two_d_bins = np.empty((bins.size - 1, 2), dtype=bins.dtype)
                two_d_bins[:, 0] = bins[:-1]
                two_d_bins[:, 1] = bins[1:]

            two_d_bins = type(self)(two_d_bins, units=bin_units)
            return d, two_d_bins

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_deprecated_kwarg_check("_preserve_partitions")
    def median(self, axes=None, squeeze=False, mtol=1, inplace=False):
        """Calculate median values.

        Calculates the median value or the median values along axes.

        See
        https://ncas-cms.github.io/cf-python/analysis.html#collapse-methods
        for mathematical definitions.

         ..seealso:: `mean_of_upper_decile`, `percentile`

        :Parameters:

            {{collapse axes: (sequence of) `int`, optional}}

            {{collapse squeeze: `bool`, optional}}

            {{mtol: number, optional}}

            {{inplace: `bool`, optional}}

        :Returns:

            `Data` or `None`
                The collapsed data, or `None` if the operation was
                in-place.

        **Examples**

        >>> a = np.ma.arange(12).reshape(4, 3)
        >>> d = cf.Data(a, 'K')
        >>> d[1, 1] = cf.masked
        >>> print(d.array)
        [[0 1 2])
         [3 -- 5]
         [6 7 8]
         [9 10 11]]
        >>> d.median()
        <CF Data(1, 1): [[6.0]] K>

        """
        return self.percentile(
            50, axes=axes, squeeze=squeeze, mtol=mtol, inplace=inplace
        )

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def mean_of_upper_decile(
        self,
        axes=None,
        weights=None,
        method="linear",
        squeeze=False,
        mtol=1,
        include_decile=True,
        split_every=None,
        inplace=False,
    ):
        """Mean of values defined by the upper tenth of their
        distribution.

        For the values defined by the upper tenth of their
        distribution, calculates their mean, or their mean along axes.

        See
        https://ncas-cms.github.io/cf-python/analysis.html#collapse-methods
        for mathematical definitions.

         ..seealso:: `mean`, `median`, `percentile`

        :Parameters:

            {{collapse axes: (sequence of) `int`, optional}}

            {{weights: data_like, `dict`, or `None`, optional}}

                .. note:: *weights* only applies to the calculation of
                          the mean defined by the upper tenth of their
                          distribution.

            {{percentile method: `str`, optional}}

                .. versionadded:: TODODASKVER

            {{collapse squeeze: `bool`, optional}}

            {{mtol: number, optional}}

                .. note:: *mtol* only applies to the calculation of
                          the location of the 90th percentile.

            include_decile: `bool`, optional
                If True then include in the mean any values that are
                equal to the 90th percentile. By default these are
                excluded.

            {{split_every: `int` or `dict`, optional}}

                .. versionadded:: TODODASKVER

            {{inplace: `bool`, optional}}

        :Returns:

            `Data` or `None`
                The collapsed data, or `None` if the operation was
                in-place.

        **Examples**

        >>> d = cf.Data(np.arange(20).reshape(4, 5), 'm')
        >>> print(d.array)
        [[ 0  1  2  3  4]
         [ 5  6  7  8  9]
         [10 11 12 13 14]
         [15 16 17 18 19]]
        >>> e = d.mean_of_upper_decile()
        >>> e
        <CF Data(1, 1): [[18.5]] m>

        """
        d = _inplace_enabled_define_and_cleanup(self)

        # Find the 90th percentile
        p90 = d.percentile(
            90, axes=axes, squeeze=False, mtol=mtol, inplace=False
        )

        # Mask all elements that are less than (or equal to) the 90th
        # percentile
        if include_decile:
            less_than_p90 = d < p90
        else:
            less_than_p90 = d <= p90

        if mtol < 1:
            # Set missing values to True to ensure that 'd' gets
            # masked at those locations
            less_than_p90.filled(True, inplace=True)

        d.where(less_than_p90, cf_masked, inplace=True)

        # Find the mean of elements greater than (or equal to) the
        # 90th percentile
        d.mean(
            axes=axes,
            weights=weights,
            squeeze=squeeze,
            mtol=1,
            split_every=split_every,
            inplace=True,
        )

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_deprecated_kwarg_check("_preserve_partitions")
    @_inplace_enabled(default=False)
    def percentile(
        self,
        ranks,
        axes=None,
        method="linear",
        squeeze=False,
        mtol=1,
        inplace=False,
        _preserve_partitions=False,
        interpolation=None,
    ):
        """Compute percentiles of the data along the specified axes.

        The default is to compute the percentiles along a flattened
        version of the data.

        If the input data are integers, or floats smaller than float64, or
        the input data contains missing values, then output data-type is
        float64. Otherwise, the output data-type is the same as that of
        the input.

        If multiple percentile ranks are given then a new, leading data
        dimension is created so that percentiles can be stored for each
        percentile rank.

        **Accuracy**

        The `percentile` method returns results that are consistent
        with `numpy.percentile`, which may be different to those
        created by `dask.percentile`. The dask method uses an
        algorithm that calculates approximate percentiles which are
        likely to be different from the correct values when there are
        two or more dask chunks.

        >>> import numpy as np
        >>> import dask.array as da
        >>> import cf
        >>> a = np.arange(101)
        >>> dx = da.from_array(a, chunks=10)
        >>> da.percentile(dx, [40, 60]).compute()
        array([40.36])
        >>> np.percentile(a, 40)
        array([40.])
        >>> d = cf.Data(a, chunks=10)
        >>> d.percentile(40).array
        array([40.])

        .. versionadded:: 3.0.4

        .. seealso:: `digitize`, `median`, `mean_of_upper_decile`,
                     `where`

        :Parameters:

            ranks: (sequence of) number
                Percentile rank, or sequence of percentile ranks, to
                compute, which must be between 0 and 100 inclusive.

            axes: (sequence of) `int`, optional
                Select the axes. The *axes* argument may be one, or a
                sequence, of integers that select the axis corresponding to
                the given position in the list of axes of the data array.

                By default, of *axes* is `None`, all axes are selected.

            {{percentile method: `str`, optional}}

                .. versionadded:: TODODASKVER

            squeeze: `bool`, optional
                If True then all axes over which percentiles are
                calculated are removed from the returned data. By default
                axes over which percentiles have been calculated are left
                in the result as axes with size 1, meaning that the result
                is guaranteed to broadcast correctly against the original
                data.

            {{mtol: number, optional}}

            {{split_every: `int` or `dict`, optional}}

                .. versionadded:: TODODASKVER

            {{inplace: `bool`, optional}}

            interpolation: deprecated at version 4.0.0
                Use the *method* parameter instead.

            _preserve_partitions: deprecated at version 4.0.0

        :Returns:

            `Data` or `None`
                The percentiles of the original data, or `None` if the
                operation was in-place.

        **Examples**

        >>> d = cf.Data(numpy.arange(12).reshape(3, 4), 'm')
        >>> print(d.array)
        [[ 0  1  2  3]
         [ 4  5  6  7]
         [ 8  9 10 11]]
        >>> p = d.percentile([20, 40, 50, 60, 80])
        >>> p
        <CF Data(5, 1, 1): [[[2.2, ..., 8.8]]] m>

        >>> p = d.percentile([20, 40, 50, 60, 80], squeeze=True)
        >>> print(p.array)
        [2.2 4.4 5.5 6.6 8.8]

        Find the standard deviation of the values above the 80th percentile:

        >>> p80 = d.percentile(80)
        <CF Data(1, 1): [[8.8]] m>
        >>> e = d.where(d<=p80, cf.masked)
        >>> print(e.array)
        [[-- -- -- --]
         [-- -- -- --]
         [-- 9 10 11]]
        >>> e.std()
        <CF Data(1, 1): [[0.816496580927726]] m>

        Find the mean of the values above the 45th percentile along the
        second axis:

        >>> p45 = d.percentile(45, axes=1)
        >>> print(p45.array)
        [[1.35],
         [5.35],
         [9.35]]
        >>> e = d.where(d<=p45, cf.masked)
        >>> print(e.array)
        [[-- -- 2 3]
         [-- -- 6 7]
         [-- -- 10 11]]
        >>> f = e.mean(axes=1)
        >>> f
        <CF Data(3, 1): [[2.5, ..., 10.5]] m>
        >>> print(f.array)
        [[ 2.5]
         [ 6.5]
         [10.5]]

        Find the histogram bin boundaries associated with given
        percentiles, and digitize the data based on these bins:

        >>> bins = d.percentile([0, 10, 50, 90, 100], squeeze=True)
        >>> print(bins.array)
        [ 0.   1.1  5.5  9.9 11. ]
        >>> e = d.digitize(bins, closed_ends=True)
        >>> print(e.array)
        [[0 0 1 1]
         [1 1 2 2]
         [2 2 3 3]]

        """
        if interpolation is not None:
            _DEPRECATION_ERROR_KWARGS(
                self,
                "percentile",
                {"interpolation": None},
                message="Use the 'method' parameter instead.",
                version="TODODASKVER",
                removed_at="5.0.0",
            )  # pragma: no cover

        d = _inplace_enabled_define_and_cleanup(self)

        # Parse percentile ranks
        q = ranks
        if not (isinstance(q, np.ndarray) or is_dask_collection(q)):
            q = np.array(ranks)

        if q.ndim > 1:
            q = q.flatten()

        if not np.issubdtype(d.dtype, np.number):
            method = "nearest"

        if axes is None:
            axes = tuple(range(d.ndim))
        else:
            axes = tuple(sorted(d._parse_axes(axes)))

        dx = d.to_dask_array()
        dtype = dx.dtype
        shape = dx.shape

        # Rechunk the data so that the dimensions over which
        # percentiles are being calculated all have one chunk.
        #
        # Make sure that no new chunks are larger (in bytes) than any
        # original chunk.
        new_chunks = normalize_chunks(
            [-1 if i in axes else "auto" for i in range(dx.ndim)],
            shape=shape,
            dtype=dtype,
            limit=dtype.itemsize * reduce(mul, map(max, dx.chunks), 1),
        )
        dx = dx.rechunk(new_chunks)

        # Initialise the indices of each chunk of the result
        #
        # E.g. [(0, 0, 0), (0, 0, 1), (0, 1, 0), (0, 1, 1)]
        keys = [key[1:] for key in flatten(dx.__dask_keys__())]

        keepdims = not squeeze
        if not keepdims:
            # Remove axes that will be dropped in the result
            indices = [i for i in range(len(keys[0])) if i not in axes]
            keys = [tuple([k[i] for i in indices]) for k in keys]

        if q.ndim:
            # Insert a leading rank dimension for non-scalar input
            # percentile ranks
            keys = [(0,) + k for k in keys]

        # Create a new dask dictionary for the result
        name = "cf-percentile-" + tokenize(dx, axes, q, method)
        name = (name,)
        dsk = {
            name
            + chunk_index: (
                cf_percentile,
                dask_key,
                q,
                axes,
                method,
                keepdims,
                mtol,
            )
            for chunk_index, dask_key in zip(keys, flatten(dx.__dask_keys__()))
        }

        # Define the chunks for the result
        if q.ndim:
            out_chunks = [(q.size,)]
        else:
            out_chunks = []

        for i, c in enumerate(dx.chunks):
            if i in axes:
                if keepdims:
                    out_chunks.append((1,))
            else:
                out_chunks.append(c)

        name = name[0]
        graph = HighLevelGraph.from_collections(name, dsk, dependencies=[dx])
        dx = Array(graph, name, chunks=out_chunks, dtype=float)

        d._set_dask(dx)

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def persist(self, inplace=False):
        """Persist the underlying dask array into memory.

        This turns an underlying lazy dask array into a equivalent
        chunked dask array, but now with the results fully computed.

        `persist` is particularly useful when using distributed
        systems, because the results will be kept in distributed
        memory, rather than returned to the local process.

        Compare with `compute` and `array`.

        **Performance**

        `persist` causes all delayed operations to be computed.

        .. versionadded:: TODODASKVER

        .. seealso:: `compute`, `array`, `datetime_array`,
                     `dask.array.Array.persist`

        :Parameters:

            {{inplace: `bool`, optional}}

        :Returns:

            `Data` or `None`
                The persisted data. If the operation was in-place then
                `None` is returned.

        **Examples**

        TODODASKDOCS

        """
        d = _inplace_enabled_define_and_cleanup(self)

        dx = self.to_dask_array()
        dx = dx.persist()
        d._set_dask(dx, delete_source=False)

        return d

    def can_compute(self, functions=None, log_levels=None, override=False):
        """Whether or not it is acceptable to compute the data.

        If the data is explicitly requested to be computed (as would
        be the case when writing to disk, or accessing the `array`
        attribute) then computation will always occur.

        This method is meant for cases when compution is desirable but
        not essential, by providing an assessment of whether
        computation would require too excessive resources (time,
        memory, and CPU), if carried out.

        By default it is considered acceptable to compute the data if
        the computed array fits in available memory and any of the
        following are true, assessed in the order given up to the
        first criterion satisfied:

        1. The `force_compute` attribute is True.

        2. The current log level is ``'DEBUG'``.

        3. Any computations stored after initialisation consist only
           subspace, concatenate, reshape, and copy functions.

        .. versionadded:: 4.0.0

        .. seealso:: `force_compute`, `cf.log_level`

        :Parameters:

            functions: (sequence of) `str`, optional
                Include the specified functions, in addition to the
                defaults, as those that will allow
                computation. Functions are identified by matching the
                beginnings of the key names in the dask graph layers,
                found with `dask.layers` attribute of the dask
                array. See the *override* parameter.

            log_level: (sequence of) `str`, optional
                Include the specified log levels, in addition to the
                default, as those that will allow compuitation. See
                the *override* parameter.

            override : `bool`, optional
                If True then only compute the data for the given
                *log_levels* (if any) and the given *functions* (if
                any), ignoring the defaults. If the `force_compute`
                attribute is True then computation occurs in any case.

        :Returns:

            `bool`
                True if acceptable to compute the data, otherwise
                False.

        """
        # TODODASKAPI - this method is premature - needs thinking about as part
        # of the wider resource management issue

        # TODODASK: Always return True for now, to aid development.
        return True

        dx = self.to_dask_array()

        # TODODASK fits in memory.

        # 1 Force compute
        if self.force_compute:
            return True

        # 2 Log levels
        if override:
            allowed_log_levels = ()
            allowed_functions = ()
        else:
            allowed_log_levels = ("DEBUG",)
            allowed_functions = (
                "array-",
                "getitem-",
                "copy-",
                "concatenate-",
                "reshape-",
            )

        if log_levels:
            if isinstance(log_levels, str):
                log_levels = (log_levels,)

            allowed_log_levels += tuple(log_levels)

        if log_level().value in allowed_log_levels:
            return True

        # 3 Stored computations
        layers = dx.dask.layers
        if len(layers) == 1:
            # No stored computations after initialisation
            return True

        if functions:
            if isinstance(functions, str):
                functions = (functions,)

            allowed_functions += tuple(allowed_functions)

        return all(
            [
                any([key.startswith(x) for x in allowed_functions])
                for key in tuple(layers)[1:]
            ]
        )

    @daskified(_DASKIFIED_VERBOSE)
    @_deprecated_kwarg_check("i")
    @_inplace_enabled(default=False)
    def ceil(self, inplace=False, i=False):
        """The ceiling of the data, element-wise.

        The ceiling of ``x`` is the smallest integer ``n``, such that
        ``n>=x``.

        .. versionadded:: 1.0

        .. seealso:: `floor`, `rint`, `trunc`

        :Parameters:

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`
                The ceiling of the data. If the operation was in-place
                then `None` is returned.

        **Examples**

        >>> d = cf.Data([-1.9, -1.5, -1.1, -1, 0, 1, 1.1, 1.5 , 1.9])
        >>> print(d.array)
        [-1.9 -1.5 -1.1 -1.   0.   1.   1.1  1.5  1.9]
        >>> print(d.ceil().array)
        [-1. -1. -1. -1.  0.  1.  2.  2.  2.]

        """
        d = _inplace_enabled_define_and_cleanup(self)
        dx = d.to_dask_array()
        d._set_dask(da.ceil(dx))
        return d

    @daskified(_DASKIFIED_VERBOSE)
    def compute(self):
        """A numpy view the data.

        In-place changes to the returned numpy array *might* affect
        the underlying dask array, depending on how the dask array has
        been defined, including any delayed operations.

        The returned numpy array has the same mask hardness and fill
        values as the data.

        Compare with `array`.

        **Performance**

        `array` causes all delayed operations to be computed.

        .. versionadded:: TODODASKVER

        .. seealso:: `persist`, `array`, `datetime_array`

        :Returns:

            `numpy.ndarray`
                The numpy view of the data.

        **Examples**

        >>> d = cf.Data([1, 2, 3.0], 'km')
        >>> d.compute()
        array([1., 2., 3.])

        """
        a = self.to_dask_array().compute()

        if np.ma.isMA(a):
            if self.hardmask:
                a.harden_mask()
            else:
                a.soften_mask()

            a.set_fill_value(self.fill_value)

        return a

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def convolution_filter(
        self,
        window=None,
        axis=None,
        mode=None,
        cval=None,
        origin=0,
        inplace=False,
    ):
        """Return the data convolved along the given axis with the
        specified filter.

        The magnitude of the integral of the filter (i.e. the sum of the
        weights defined by the *weights* parameter) affects the convolved
        values. For example, filter weights of ``[0.2, 0.2 0.2, 0.2,
        0.2]`` will produce a non-weighted 5-point running mean; and
        weights of ``[1, 1, 1, 1, 1]`` will produce a 5-point running
        sum. Note that the weights returned by functions of the
        `scipy.signal.windows` package do not necessarily sum to 1 (see
        the examples for details).

        .. versionadded:: 3.3.0

        :Parameters:

            window: sequence of numbers
                Specify the window of weights to use for the filter.

                *Parameter example:*
                  An unweighted 5-point moving average can be computed
                  with ``weights=[0.2, 0.2, 0.2, 0.2, 0.2]``

                Note that the `scipy.signal.windows` package has suite of
                window functions for creating weights for filtering (see
                the examples for details).

            axis: `int`
                Select the axis over which the filter is to be applied.
                removed. The *axis* parameter is an integer that selects
                the axis corresponding to the given position in the list
                of axes of the data.

                *Parameter example:*
                  Convolve the second axis: ``axis=1``.

                *Parameter example:*
                  Convolve the last axis: ``axis=-1``.

            mode: `str`, optional
                The *mode* parameter determines how the input array is
                extended when the filter overlaps an array border. The
                default value is ``'constant'`` or, if the dimension being
                convolved is cyclic (as ascertained by the `iscyclic`
                method), ``'wrap'``. The valid values and their behaviours
                are as follows:

                ==============  ==========================  ============================
                *mode*          Description                 Behaviour
                ==============  ==========================  ============================
                ``'reflect'``   The input is extended by    ``(c b a | a b c | c b a)``
                                reflecting about the edge

                ``'constant'``  The input is extended by    ``(k k k | a b c | k k k)``
                                filling all values beyond
                                the edge with the same
                                constant value (``k``),
                                defined by the *cval*
                                parameter.

                ``'nearest'``   The input is extended by    ``(a a a | a b c | c c c )``
                                replicating the last point

                ``'mirror'``    The input is extended by    ``(c b | a b c | b a)``
                                reflecting about the
                                centre of the last point.

                ``'wrap'``      The input is extended by    ``(a b c | a b c | a b c)``
                                wrapping around to the
                                opposite edge.

                ``'periodic'``  This is a synonym for
                                ``'wrap'``.
                ==============  ==========================  ============================

                The position of the window relative to each value can be
                changed by using the *origin* parameter.

            cval: scalar, optional
                Value to fill past the edges of the array if *mode* is
                ``'constant'``. Defaults to `None`, in which case the
                edges of the array will be filled with missing data.

                *Parameter example:*
                   To extend the input by filling all values beyond the
                   edge with zero: ``cval=0``

            origin: `int`, optional
                Controls the placement of the filter. Defaults to 0, which
                is the centre of the window. If the window has an even
                number of weights then then a value of 0 defines the index
                defined by ``width/2 -1``.

                *Parameter example:*
                  For a weighted moving average computed with a weights
                  window of ``[0.1, 0.15, 0.5, 0.15, 0.1]``, if
                  ``origin=0`` then the average is centred on each
                  point. If ``origin=-2`` then the average is shifted to
                  include the previous four points. If ``origin=1`` then
                  the average is shifted to include the previous point and
                  the and the next three points.

            {{inplace: `bool`, optional}}

        :Returns:

            `Data` or `None`
                The convolved data, or `None` if the operation was
                in-place.

        """
        from .dask_utils import cf_convolve1d

        d = _inplace_enabled_define_and_cleanup(self)

        iaxis = d._parse_axes(axis)
        if len(iaxis) != 1:
            raise ValueError(
                "Must specify a unique domain axis with the 'axis' "
                f"parameter. {axis!r} specifies axes {iaxis!r}"
            )

        iaxis = iaxis[0]

        if mode is None:
            # Default mode is 'wrap' if the axis is cyclic, or else
            # 'constant'.
            if iaxis in d.cyclic():
                boundary = "periodic"
            else:
                boundary = cval
        elif mode == "wrap":
            boundary = "periodic"
        elif mode == "constant":
            boundary = cval
        elif mode == "mirror":
            raise ValueError(
                "'mirror' mode is no longer available. Please raise an "
                "issue at https://github.com/NCAS-CMS/cf-python/issues "
                "if you would like it to be re-implemented."
            )
            # This re-implementation would involve getting a 'mirror'
            # function added to dask.array.overlap, along similar
            # lines to the existing 'reflect' function in that module.
        else:
            boundary = mode

        # Set the overlap depth large enough to accommodate the
        # filter.
        #
        # For instance, for a 5-point window, the calculated value at
        # each point requires 2 points either side if the filter is
        # centred (i.e. origin is 0) and (up to) 3 points either side
        # if origin is 1 or -1.
        #
        # It is a restriction of dask.array.map_overlap that we can't
        # use asymmetric halos for general 'boundary' types.
        size = len(window)
        depth = int(size / 2)
        if not origin and not size % 2:
            depth += 1

        depth += abs(origin)

        dx = d.to_dask_array()

        # Cast to float to ensure that NaNs can be stored (as required
        # by cf_convolve1d)
        if dx.dtype != float:
            dx = dx.astype(float, copy=False)

        # Convolve each chunk
        convolve1d = partial(
            cf_convolve1d, window=window, axis=iaxis, origin=origin
        )

        dx = dx.map_overlap(
            convolve1d,
            depth={iaxis: depth},
            boundary=boundary,
            trim=True,
            meta=np.array((), dtype=float),
        )

        d._set_dask(dx)

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def cumsum(
        self,
        axis=None,
        masked_as_zero=False,
        method="sequential",
        inplace=False,
    ):
        """Return the data cumulatively summed along the given axis.

        .. versionadded:: 3.0.0

        .. seealso:: `diff`, `sum`

        :Parameters:

            axis: `int`, optional
                Select the axis over which the cumulative sums are to
                be calculated. By default the cumulative sum is
                computed over the flattened array.

            method: `str`, optional
                Choose which method to use to perform the cumulative
                sum. See `dask.array.cumsum` for details.

                .. versionadded:: TODODASKVER

            {{inplace: `bool`, optional}}

                .. versionadded:: 3.3.0

            masked_as_zero: deprecated at version TODODASKVER
                See the examples for the new behaviour when there are
                masked values.

        :Returns:

             `Data` or `None`
                The data with the cumulatively summed axis, or `None`
                if the operation was in-place.

        **Examples**

        >>> d = cf.Data(numpy.arange(12).reshape(3, 4))
        >>> print(d.array)
        [[ 0  1  2  3]
         [ 4  5  6  7]
         [ 8  9 10 11]]
        >>> print(d.cumsum().array)
        [ 0  1  3  6 10 15 21 28 36 45 55 66]
        >>> print(d.cumsum(axis=0).array)
        [[ 0  1  2  3]
         [ 4  6  8 10]
         [12 15 18 21]]
        >>> print(d.cumsum(axis=1).array)
        [[ 0  1  3  6]
         [ 4  9 15 22]
         [ 8 17 27 38]]

        >>> d[0, 0] = cf.masked
        >>> d[1, [1, 3]] = cf.masked
        >>> d[2, 0:2] = cf.masked
        >>> print(d.array)
        [[-- 1 2 3]
         [4 -- 6 --]
         [-- -- 10 11]]
        >>> print(d.cumsum(axis=0).array)
        [[-- 1 2 3]
         [4 -- 8 --]
         [-- -- 18 14]]
        >>> print(d.cumsum(axis=1).array)
        [[-- 1 3 6]
         [4 -- 10 --]
         [-- -- 10 21]]

        """
        if masked_as_zero:
            _DEPRECATION_ERROR_KWARGS(
                self,
                "cumsum",
                {"masked_as_zero": None},
                message="",
                version="TODODASKVER",
                removed_at="5.0.0",
            )  # pragma: no cover

        d = _inplace_enabled_define_and_cleanup(self)

        dx = d.to_dask_array()
        dx = dx.cumsum(axis=axis, method=method)
        d._set_dask(dx)

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def rechunk(
        self,
        chunks=_DEFAULT_CHUNKS,
        threshold=None,
        block_size_limit=None,
        balance=False,
        inplace=False,
    ):
        """Change the chunk structure of the data.

        .. versionadded:: TODODASKVER

        .. seealso:: `chunks`, `dask.array.rechunk`

        :Parameters:

            {{chunks: `int`, `tuple`, `dict` or `str`, optional}}

            threshold: `int`, optional
                The graph growth factor under which we don't bother
                introducing an intermediate step. See
                `dask.array.rechunk` for details.

            block_size_limit: `int`, optional
                The maximum block size (in bytes) we want to produce,
                as defined by the `cf.chunksize` function.

            balance: `bool`, optional
                If True, try to make each chunk the same size. By
                default this is not attempted.

                This means ``balance=True`` will remove any small
                leftover chunks, so using ``d.rechunk(chunks=len(d) //
                N, balance=True)`` will almost certainly result in
                ``N`` chunks.

        :Returns:

            `Data` or `None`
                The rechunked data, or `None` if the operation was
                in-place.

        **Examples**

        >>> x = cf.Data.ones((1000, 1000), chunks=(100, 100))

        Specify uniform chunk sizes with a tuple

        >>> y = x.rechunk((1000, 10))

        Or chunk only specific dimensions with a dictionary

        >>> y = x.rechunk({0: 1000})

        Use the value ``-1`` to specify that you want a single chunk
        along a dimension or the value ``"auto"`` to specify that dask
        can freely rechunk a dimension to attain blocks of a uniform
        block size.

        >>> y = x.rechunk({0: -1, 1: 'auto'}, block_size_limit=1e8)

        If a chunk size does not divide the dimension then rechunk
        will leave any unevenness to the last chunk.

        >>> x.rechunk(chunks=(400, -1)).chunks
        ((400, 400, 200), (1000,))

        However if you want more balanced chunks, and don't mind
        `dask` choosing a different chunksize for you then you can use
        the ``balance=True`` option.

        >>> x.rechunk(chunks=(400, -1), balance=True).chunks
        ((500, 500), (1000,))

        """
        d = _inplace_enabled_define_and_cleanup(self)

        dx = d.to_dask_array()
        dx = dx.rechunk(chunks, threshold, block_size_limit, balance)
        d._set_dask(dx, delete_source=False)

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def _asdatetime(self, inplace=False):
        """Change the internal representation of data array elements
        from numeric reference times to datetime-like objects.

        If the calendar has not been set then the default CF calendar will
        be used and the units' and the `calendar` attribute will be
        updated accordingly.

        If the internal representations are already datetime-like objects
        then no change occurs.

        .. versionadded:: 1.3

        .. seealso:: `_asreftime`, `_isdatetime`

        :Parameters:

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`

        **Examples**

        >>> d = cf.Data([[1.93, 5.17]], "days since 2000-12-29")
        >>> e = d._asdatetime()
        >>> print(e.array)
        [[cftime.DatetimeGregorian(2000, 12, 30, 22, 19, 12, 0, has_year_zero=False)
          cftime.DatetimeGregorian(2001, 1, 3, 4, 4, 48, 0, has_year_zero=False)]]
        >>> f = e._asreftime()
        >>> print(f.array)
        [[1.93 5.17]]

        """
        d = _inplace_enabled_define_and_cleanup(self)

        units = d.Units
        if not units.isreftime:
            raise ValueError(
                f"Can't convert {units!r} values to date-time objects"
            )

        if not d._isdatetime():
            dx = d.to_dask_array()
            dx = dx.map_blocks(cf_rt2dt, units=units, dtype=object)
            d._set_dask(dx)

        return d

    @daskified(_DASKIFIED_VERBOSE)
    def _isdatetime(self):
        """True if the internal representation is a datetime object."""
        return self.dtype.kind == "O" and self.Units.isreftime

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def _asreftime(self, inplace=False):
        """Change the internal representation of data array elements
        from datetime-like objects to numeric reference times.

        If the calendar has not been set then the default CF calendar will
        be used and the units' and the `calendar` attribute will be
        updated accordingly.

        If the internal representations are already numeric reference
        times then no change occurs.

        .. versionadded:: 1.3

        .. seealso:: `_asdatetime`, `_isdatetime`

        :Parameters:

            {{inplace: `bool`, optional}}

        :Returns:

            `Data` or `None`

        **Examples**

        >>> d = cf.Data([[1.93, 5.17]], "days since 2000-12-29")
        >>> e = d._asdatetime()
        >>> print(e.array)
        [[cftime.DatetimeGregorian(2000, 12, 30, 22, 19, 12, 0, has_year_zero=False)
          cftime.DatetimeGregorian(2001, 1, 3, 4, 4, 48, 0, has_year_zero=False)]]
        >>> f = e._asreftime()
        >>> print(f.array)
        [[1.93 5.17]]

        """
        d = _inplace_enabled_define_and_cleanup(self)

        units = d.Units
        if not units.isreftime:
            raise ValueError(
                f"Can't convert {units!r} values to numeric reference times"
            )

        if d._isdatetime():
            dx = d.to_dask_array()
            dx = dx.map_blocks(cf_dt2rt, units=units, dtype=float)
            d._set_dask(dx)

        return d

    def _combined_units(self, data1, method, inplace):
        """Combines by given method the data's units with other units.

        :Parameters:

            data1: `Data`

            method: `str`

            {{inplace: `bool`, optional}}

        :Returns:

            `Data` or `None`, `Data` or `None`, `Units`

        **Examples**

        >>> d._combined_units(e, '__sub__')
        >>> d._combined_units(e, '__imul__')
        >>> d._combined_units(e, '__irdiv__')
        >>> d._combined_units(e, '__lt__')
        >>> d._combined_units(e, '__rlshift__')
        >>> d._combined_units(e, '__iand__')

        """
        method_type = method[-5:-2]

        data0 = self

        units0 = data0.Units
        units1 = data1.Units

        if not units0 and not units1:
            return data0, data1, units0
        if (
            units0.isreftime
            and units1.isreftime
            and not units0.equivalent(units1)
        ):
            # Both are reference_time, but have non-equivalent
            # calendars
            if units0._canonical_calendar and not units1._canonical_calendar:
                data1 = data1._asdatetime()
                data1.override_units(units0, inplace=True)
                data1._asreftime(inplace=True)
                units1 = units0
            elif units1._canonical_calendar and not units0._canonical_calendar:
                if not inplace:
                    inplace = True
                    data0 = data0.copy()
                data0._asdatetime(inplace=True)
                data0.override_units(units1, inplace=True)
                data0._asreftime(inplace=True)
                units0 = units1
        # --- End: if

        if method_type in ("_eq", "_ne", "_lt", "_le", "_gt", "_ge"):
            # ---------------------------------------------------------
            # Operator is one of ==, !=, >=, >, <=, <
            # ---------------------------------------------------------
            if units0.equivalent(units1):
                # Units are equivalent
                if not units0.equals(units1):
                    data1 = data1.copy()
                    data1.Units = units0
                return data0, data1, _units_None
            elif not units1 or not units0:
                # At least one of the units is undefined
                return data0, data1, _units_None
            else:
                raise ValueError(
                    "Can't compare {0!r} to {1!r}".format(units0, units1)
                )
        # --- End: if

        # still here?
        if method_type in ("and", "_or", "ior", "ror", "xor", "ift"):
            # ---------------------------------------------------------
            # Operation is one of &, |, ^, >>, <<
            # ---------------------------------------------------------
            if units0.equivalent(units1):
                # Units are equivalent
                if not units0.equals(units1):
                    data1 = data1.copy()
                    data1.Units = units0
                return data0, data1, units0
            elif not units1:
                # units1 is undefined
                return data0, data1, units0
            elif not units0:
                # units0 is undefined
                return data0, data1, units1
            else:
                # Both units are defined and not equivalent
                raise ValueError(
                    "Can't operate with {} on data with {!r} to {!r}".format(
                        method, units0, units1
                    )
                )
        # --- End: if

        # Still here?
        if units0.isreftime:
            # ---------------------------------------------------------
            # units0 is reference time
            # ---------------------------------------------------------
            if method_type == "sub":
                if units1.isreftime:
                    if units0.equivalent(units1):
                        # Equivalent reference_times: the output units
                        # are time
                        if not units0.equals(units1):
                            data1 = data1.copy()
                            data1.Units = units0
                        return data0, data1, Units(_ut_unit=units0._ut_unit)
                    else:
                        # Non-equivalent reference_times: raise an
                        # exception
                        getattr(units0, method)(units1)
                elif units1.istime:
                    # reference_time minus time: the output units are
                    # reference_time
                    time0 = Units(_ut_unit=units0._ut_unit)
                    if not units1.equals(time0):
                        data1 = data1.copy()
                        data1.Units = time0
                    return data0, data1, units0
                elif not units1:
                    # reference_time minus no_units: the output units
                    # are reference_time
                    return data0, data1, units0
                else:
                    # reference_time minus something not yet accounted
                    # for: raise an exception
                    getattr(units0, method)(units1)

            elif method_type in ("add", "mul", "div", "mod"):
                if units1.istime:
                    # reference_time plus regular_time: the output
                    # units are reference_time
                    time0 = Units(_ut_unit=units0._ut_unit)
                    if not units1.equals(time0):
                        data1 = data1.copy()
                        data1.Units = time0
                    return data0, data1, units0
                elif not units1:
                    # reference_time plus no_units: the output units
                    # are reference_time
                    return data0, data1, units0
                else:
                    # reference_time plus something not yet accounted
                    # for: raise an exception
                    getattr(units0, method)(units1)

            else:
                # Raise an exception
                getattr(units0, method)(units1)

        elif units1.isreftime:
            # ---------------------------------------------------------
            # units1 is reference time
            # ---------------------------------------------------------
            if method_type == "add":
                if units0.istime:
                    # Time plus reference_time: the output units are
                    # reference_time
                    time1 = Units(_ut_unit=units1._ut_unit)
                    if not units0.equals(time1):
                        if not inplace:
                            data0 = data0.copy()
                        data0.Units = time1
                    return data0, data1, units1
                elif not units0:
                    # No_units plus reference_time: the output units
                    # are reference_time
                    return data0, data1, units1
                else:
                    # Raise an exception
                    getattr(units0, method)(units1)
        # --- End: if

        # Still here?
        if method_type in ("mul", "div"):
            # ---------------------------------------------------------
            # Method is one of *, /, //
            # ---------------------------------------------------------
            if not units1:
                # units1 is undefined
                return data0, data1, getattr(units0, method)(_units_1)
            elif not units0:
                # units0 is undefined
                return data0, data1, getattr(_units_1, method)(units1)
                #  !!!!!!! units0*units0 YOWSER
            else:
                # Both units are defined (note: if the units are
                # noncombinable then this will raise an exception)
                return data0, data1, getattr(units0, method)(units1)
        # --- End: if

        # Still here?
        if method_type in ("sub", "add", "mod"):
            # ---------------------------------------------------------
            # Operator is one of +, -
            # ---------------------------------------------------------
            if units0.equivalent(units1):
                # Units are equivalent
                if not units0.equals(units1):
                    data1 = data1.copy()
                    data1.Units = units0
                return data0, data1, units0
            elif not units1:
                # units1 is undefined
                return data0, data1, units0
            elif not units0:
                # units0 is undefined
                return data0, data1, units1
            else:
                # Both units are defined and not equivalent (note: if
                # the units are noncombinable then this will raise an
                # exception)
                return data0, data1, getattr(units0, method)(units1)
        # --- End: if

        # Still here?
        if method_type == "pow":
            if method == "__rpow__":
                # -----------------------------------------------------
                # Operator is __rpow__
                # -----------------------------------------------------
                if not units1:
                    # units1 is undefined
                    if not units0:
                        # units0 is undefined
                        return data0, data1, _units_None
                    elif units0.isdimensionless:
                        # units0 is dimensionless
                        if not units0.equals(_units_1):
                            if not inplace:
                                data0 = data0.copy()
                            data0.Units = _units_1

                        return data0, data1, _units_None
                elif units1.isdimensionless:
                    # units1 is dimensionless
                    if not units1.equals(_units_1):
                        data1 = data1.copy()
                        data1.Units = _units_1

                    if not units0:
                        # units0 is undefined
                        return data0, data1, _units_1
                    elif units0.isdimensionless:
                        # units0 is dimensionless
                        if not units0.equals(_units_1):
                            if not inplace:
                                data0 = data0.copy()
                            data0.Units = _units_1

                        return data0, data1, _units_1
                else:
                    # units1 is defined and is not dimensionless
                    if data0._size > 1:
                        raise ValueError(
                            "Can only raise units to the power of a single "
                            "value at a time. Asking to raise to the power of "
                            "{}".format(data0)
                        )

                    if not units0:
                        # Check that the units are not shifted, as
                        # raising this to a power is a nonlinear
                        # operation
                        p = data0.datum(0)
                        if units0 != (units0**p) ** (1.0 / p):
                            raise ValueError(
                                "Can't raise shifted units {!r} to the "
                                "power {}".format(units0, p)
                            )

                        return data0, data1, units1**p
                    elif units0.isdimensionless:
                        # units0 is dimensionless
                        if not units0.equals(_units_1):
                            if not inplace:
                                data0 = data0.copy()
                            data0.Units = _units_1

                        # Check that the units are not shifted, as
                        # raising this to a power is a nonlinear
                        # operation
                        p = data0.datum(0)
                        if units0 != (units0**p) ** (1.0 / p):
                            raise ValueError(
                                "Can't raise shifted units {!r} to the "
                                "power {}".format(units0, p)
                            )

                        return data0, data1, units1**p
                # --- End: if

                # This will deliberately raise an exception
                units1**units0
            else:
                # -----------------------------------------------------
                # Operator is __pow__
                # -----------------------------------------------------
                if not units0:
                    # units0 is undefined
                    if not units1:
                        # units0 is undefined
                        return data0, data1, _units_None
                    elif units1.isdimensionless:
                        # units0 is dimensionless
                        if not units1.equals(_units_1):
                            data1 = data1.copy()
                            data1.Units = _units_1

                        return data0, data1, _units_None
                elif units0.isdimensionless:
                    # units0 is dimensionless
                    if not units0.equals(_units_1):
                        if not inplace:
                            data0 = data0.copy()
                        data0.Units = _units_1

                    if not units1:
                        # units1 is undefined
                        return data0, data1, _units_1
                    elif units1.isdimensionless:
                        # units1 is dimensionless
                        if not units1.equals(_units_1):
                            data1 = data1.copy()
                            data1.Units = _units_1

                        return data0, data1, _units_1
                else:
                    # units0 is defined and is not dimensionless
                    if data1._size > 1:
                        raise ValueError(
                            "Can only raise units to the power of a single "
                            "value at a time. Asking to raise to the power of "
                            "{}".format(data1)
                        )

                    if not units1:
                        # Check that the units are not shifted, as
                        # raising this to a power is a nonlinear
                        # operation
                        p = data1.datum(0)
                        if units0 != (units0**p) ** (1.0 / p):
                            raise ValueError(
                                "Can't raise shifted units {!r} to the "
                                "power {}".format(units0, p)
                            )

                        return data0, data1, units0**p
                    elif units1.isdimensionless:
                        # units1 is dimensionless
                        if not units1.equals(_units_1):
                            data1 = data1.copy()
                            data1.Units = _units_1

                        # Check that the units are not shifted, as
                        # raising this to a power is a nonlinear
                        # operation
                        p = data1.datum(0)
                        if units0 != (units0**p) ** (1.0 / p):
                            raise ValueError(
                                "Can't raise shifted units {!r} to the "
                                "power {}".format(units0, p)
                            )

                        return data0, data1, units0**p
                # --- End: if

                # This will deliberately raise an exception
                units0**units1
            # --- End: if
        # --- End: if

        # Still here?
        raise ValueError(
            "Can't operate with {} on data with {!r} to {!r}".format(
                method, units0, units1
            )
        )

    @daskified(_DASKIFIED_VERBOSE)
    def _binary_operation(self, other, method):
        """Implement binary arithmetic and comparison operations with
        the numpy broadcasting rules.

        It is called by the binary arithmetic and comparison
        methods, such as `__sub__`, `__imul__`, `__rdiv__`, `__lt__`, etc.

        .. seealso:: `_unary_operation`

        :Parameters:

            other:
                The object on the right hand side of the operator.

            method: `str`
                The binary arithmetic or comparison method name (such as
                ``'__imul__'`` or ``'__ge__'``).

        :Returns:

            `Data`
                A new data object, or if the operation was in place, the
                same data object.

        **Examples**

        >>> d = cf.Data([0, 1, 2, 3])
        >>> e = cf.Data([1, 1, 3, 4])

        >>> f = d._binary_operation(e, '__add__')
        >>> print(f.array)
        [1 2 5 7]

        >>> e = d._binary_operation(e, '__lt__')
        >>> print(e.array)
        [ True False  True  True]

        >>> d._binary_operation(2, '__imul__')
        >>> print(d.array)
        [0 2 4 6]

        """
        if getattr(other, "_NotImplemented_RHS_Data_op", False):
            return NotImplemented

        inplace = method[2] == "i"

        # ------------------------------------------------------------
        # Ensure other is an independent Data object, for example
        # so that combination with cf.Query objects works.
        # ------------------------------------------------------------
        if not isinstance(other, self.__class__):
            if (
                isinstance(other, cftime.datetime)
                and other.calendar == ""
                and self.Units.isreftime
            ):
                other = cf_dt(
                    other, calendar=getattr(self.Units, "calendar", "standard")
                )
            elif other is None:
                # Can't sensibly initialize a Data object from a bare
                # `None` (issue #281)
                other = np.array(None, dtype=object)

            other = type(self).asdata(other)

        # ------------------------------------------------------------
        # Prepare data0 (i.e. self copied) and data1 (i.e. other)
        # ------------------------------------------------------------
        data0 = self.copy()

        # Parse units
        data0, other, new_Units = data0._combined_units(other, method, True)

        # Cast as dask arrays
        dx0 = data0.to_dask_array()
        dx1 = other.to_dask_array()

        # Set if applicable the tolerance levels for the result
        if method in ("__eq__", "__ne__"):
            rtol = self._rtol
            atol = self._atol

        # ------------------------------------------------------------
        # Perform the binary operation with data0 (self) and data1
        # (other)
        # ------------------------------------------------------------
        if method == "__eq__":
            if dx0.dtype.kind in "US" or dx1.dtype.kind in "US":
                result = getattr(dx0, method)(dx1)
            else:
                result = da.isclose(dx0, dx1, rtol=rtol, atol=atol)
        elif method == "__ne__":
            if dx0.dtype.kind in "US" or dx1.dtype.kind in "US":
                result = getattr(dx0, method)(dx1)
            else:
                result = ~da.isclose(dx0, dx1, rtol=rtol, atol=atol)
        elif inplace:
            # Find non-in-place equivalent operator (remove 'i')
            equiv_method = method[:2] + method[3:]
            result = getattr(dx0, equiv_method)(dx1)
        else:
            result = getattr(dx0, method)(dx1)

        if inplace:  # in-place so concerns original self
            self._set_dask(result)
            self.override_units(new_Units, inplace=True)
            return self
        else:  # not, so concerns a new Data object copied from self, data0
            data0._set_dask(result)
            data0.override_units(new_Units, inplace=True)
            return data0

    def _parse_indices(self, *args, **kwargs):
        """'cf.Data._parse_indices' is not available.

        Use function `cf.parse_indices` instead.

        """
        raise NotImplementedError(
            "'cf.Data._parse_indices' is not available. "
            "Use function 'cf.parse_indices' instead."
        )

    @classmethod
    @daskified(_DASKIFIED_VERBOSE)
    def concatenate(cls, data, axis=0, _preserve=True):
        """Join a sequence of data arrays together.

        :Parameters:

            data: sequence of `Data`
                The data arrays to be concatenated. Concatenation is
                carried out in the order given. Each data array must have
                equivalent units and the same shape, except in the
                concatenation axis. Note that scalar arrays are treated as
                if they were one dimensional.

            axis: `int`, optional
                The axis along which the arrays will be joined. The
                default is 0. Note that scalar arrays are treated as if
                they were one dimensional.

                .. note:: If the axis specified is cyclic, it will become
                          non-cyclic in the output.

            _preserve: `bool`, optional
                Deprecated at version TODODASKVER.

        :Returns:

            `Data`
                The concatenated data.

        **Examples**

        >>> d = cf.Data([[1, 2], [3, 4]], 'km')
        >>> e = cf.Data([[5.0, 6.0]], 'metre')
        >>> f = cf.Data.concatenate((d, e))
        >>> print(f.array)
        [[ 1.     2.   ]
         [ 3.     4.   ]
         [ 0.005  0.006]]
        >>> f.equals(cf.Data.concatenate((d, e), axis=-2))
        True

        >>> e = cf.Data([[5.0], [6.0]], 'metre')
        >>> f = cf.Data.concatenate((d, e), axis=1)
        >>> print(f.array)
        [[ 1.     2.     0.005]
         [ 3.     4.     0.006]]

        >>> d = cf.Data(1, 'km')
        >>> e = cf.Data(50.0, 'metre')
        >>> f = cf.Data.concatenate((d, e))
        >>> print(f.array)
        [ 1.    0.05]

        >>> e = cf.Data([50.0, 75.0], 'metre')
        >>> f = cf.Data.concatenate((d, e))
        >>> print(f.array)
        [ 1.     0.05   0.075]

        """
        data = tuple(data)
        if len(data) < 2:
            raise ValueError(
                "Can't concatenate: Must provide at least two data arrays"
            )

        data0 = data[0].copy()

        processed_data = []
        units0 = data0.Units
        for index, data1 in enumerate(data):
            copied = False  # to avoid making two copies in a given case

            # Turn any scalar array into a 1-d array
            if not data1.ndim:
                data1 = data1.copy()
                copied = True
                data1.insert_dimension(inplace=True)

            # Check and conform, if necessary, the units of all inputs
            if not units0.equivalent(data1.Units):
                raise ValueError(
                    "Can't concatenate: All the input arrays must have "
                    "equivalent units"
                )
            elif not units0.equals(data1.Units):  # conform for consistency
                if not copied:
                    data1 = data1.copy()
                data1.Units = units0

            processed_data.append(data1)

        # Get data as dask arrays and apply concatenation operation
        dxs = []
        for data1 in processed_data:
            dxs.append(data1.to_dask_array())

        data0._set_dask(da.concatenate(dxs, axis=axis))

        # Manage cyclicity of axes: if join axis was cyclic, it is no longer
        axis = data0._parse_axes(axis)[0]
        if axis in data0.cyclic():
            logger.warning(
                f"Concatenating along a cyclic axis ({axis}) therefore the "
                f"axis has been set as non-cyclic in the output."
            )
            data0.cyclic(axes=axis, iscyclic=False)

        return data0

    @daskified(_DASKIFIED_VERBOSE)
    def _unary_operation(self, operation):
        """Implement unary arithmetic operations.

        It is called by the unary arithmetic methods, such as
        __abs__().

        .. seealso:: `_binary_operation`

        :Parameters:

            operation: `str`
                The unary arithmetic method name (such as "__invert__").

        :Returns:

            `Data`
                A new Data array.

        **Examples**

        >>> d = cf.Data([[1, 2, -3, -4, -5]])

        >>> e = d._unary_operation('__abs__')
        >>> print(e.array)
        [[1 2 3 4 5]]

        >>> e = d.__abs__()
        >>> print(e.array)
        [[1 2 3 4 5]]

        >>> e = abs(d)
        >>> print(e.array)
        [[1 2 3 4 5]]

        """
        out = self.copy(array=False)

        dx = self.to_dask_array()
        dx = getattr(operator, operation)(dx)

        out._set_dask(dx)

        return out

    @daskified(_DASKIFIED_VERBOSE)
    def __add__(self, other):
        """The binary arithmetic operation ``+``

        x.__add__(y) <==> x+y

        """
        return self._binary_operation(other, "__add__")

    @daskified(_DASKIFIED_VERBOSE)
    def __iadd__(self, other):
        """The augmented arithmetic assignment ``+=``

        x.__iadd__(y) <==> x+=y

        """
        return self._binary_operation(other, "__iadd__")

    @daskified(_DASKIFIED_VERBOSE)
    def __radd__(self, other):
        """The binary arithmetic operation ``+`` with reflected
        operands.

        x.__radd__(y) <==> y+x

        """
        return self._binary_operation(other, "__radd__")

    @daskified(_DASKIFIED_VERBOSE)
    def __sub__(self, other):
        """The binary arithmetic operation ``-``

        x.__sub__(y) <==> x-y

        """
        return self._binary_operation(other, "__sub__")

    @daskified(_DASKIFIED_VERBOSE)
    def __isub__(self, other):
        """The augmented arithmetic assignment ``-=``

        x.__isub__(y) <==> x-=y

        """
        return self._binary_operation(other, "__isub__")

    @daskified(_DASKIFIED_VERBOSE)
    def __rsub__(self, other):
        """The binary arithmetic operation ``-`` with reflected
        operands.

        x.__rsub__(y) <==> y-x

        """
        return self._binary_operation(other, "__rsub__")

    @daskified(_DASKIFIED_VERBOSE)
    def __mul__(self, other):
        """The binary arithmetic operation ``*``

        x.__mul__(y) <==> x*y

        """
        return self._binary_operation(other, "__mul__")

    @daskified(_DASKIFIED_VERBOSE)
    def __imul__(self, other):
        """The augmented arithmetic assignment ``*=``

        x.__imul__(y) <==> x*=y

        """
        return self._binary_operation(other, "__imul__")

    @daskified(_DASKIFIED_VERBOSE)
    def __rmul__(self, other):
        """The binary arithmetic operation ``*`` with reflected
        operands.

        x.__rmul__(y) <==> y*x

        """
        return self._binary_operation(other, "__rmul__")

    @daskified(_DASKIFIED_VERBOSE)
    def __div__(self, other):
        """The binary arithmetic operation ``/``

        x.__div__(y) <==> x/y

        """
        return self._binary_operation(other, "__div__")

    @daskified(_DASKIFIED_VERBOSE)
    def __idiv__(self, other):
        """The augmented arithmetic assignment ``/=``

        x.__idiv__(y) <==> x/=y

        """
        return self._binary_operation(other, "__idiv__")

    @daskified(_DASKIFIED_VERBOSE)
    def __rdiv__(self, other):
        """The binary arithmetic operation ``/`` with reflected
        operands.

        x.__rdiv__(y) <==> y/x

        """
        return self._binary_operation(other, "__rdiv__")

    @daskified(_DASKIFIED_VERBOSE)
    def __floordiv__(self, other):
        """The binary arithmetic operation ``//``

        x.__floordiv__(y) <==> x//y

        """
        return self._binary_operation(other, "__floordiv__")

    @daskified(_DASKIFIED_VERBOSE)
    def __ifloordiv__(self, other):
        """The augmented arithmetic assignment ``//=``

        x.__ifloordiv__(y) <==> x//=y

        """
        return self._binary_operation(other, "__ifloordiv__")

    @daskified(_DASKIFIED_VERBOSE)
    def __rfloordiv__(self, other):
        """The binary arithmetic operation ``//`` with reflected
        operands.

        x.__rfloordiv__(y) <==> y//x

        """
        return self._binary_operation(other, "__rfloordiv__")

    @daskified(_DASKIFIED_VERBOSE)
    def __truediv__(self, other):
        """The binary arithmetic operation ``/`` (true division)

        x.__truediv__(y) <==> x/y

        """
        return self._binary_operation(other, "__truediv__")

    @daskified(_DASKIFIED_VERBOSE)
    def __itruediv__(self, other):
        """The augmented arithmetic assignment ``/=`` (true division)

        x.__itruediv__(y) <==> x/=y

        """
        return self._binary_operation(other, "__itruediv__")

    @daskified(_DASKIFIED_VERBOSE)
    def __rtruediv__(self, other):
        """The binary arithmetic operation ``/`` (true division) with
        reflected operands.

        x.__rtruediv__(y) <==> y/x

        """
        return self._binary_operation(other, "__rtruediv__")

    @daskified(_DASKIFIED_VERBOSE)
    def __pow__(self, other, modulo=None):
        """The binary arithmetic operations ``**`` and ``pow``

        x.__pow__(y) <==> x**y

        """
        if modulo is not None:
            raise NotImplementedError(
                "3-argument power not supported for {!r}".format(
                    self.__class__.__name__
                )
            )

        return self._binary_operation(other, "__pow__")

    @daskified(_DASKIFIED_VERBOSE)
    def __ipow__(self, other, modulo=None):
        """The augmented arithmetic assignment ``**=``

        x.__ipow__(y) <==> x**=y

        """
        if modulo is not None:
            raise NotImplementedError(
                "3-argument power not supported for {!r}".format(
                    self.__class__.__name__
                )
            )

        return self._binary_operation(other, "__ipow__")

    @daskified(_DASKIFIED_VERBOSE)
    def __rpow__(self, other, modulo=None):
        """The binary arithmetic operations ``**`` and ``pow`` with
        reflected operands.

        x.__rpow__(y) <==> y**x

        """
        if modulo is not None:
            raise NotImplementedError(
                "3-argument power not supported for {!r}".format(
                    self.__class__.__name__
                )
            )

        return self._binary_operation(other, "__rpow__")

    @daskified(_DASKIFIED_VERBOSE)
    def __mod__(self, other):
        """The binary arithmetic operation ``%``

        x.__mod__(y) <==> x % y

        """
        return self._binary_operation(other, "__mod__")

    @daskified(_DASKIFIED_VERBOSE)
    def __imod__(self, other):
        """The binary arithmetic operation ``%=``

        x.__imod__(y) <==> x %= y

        """
        return self._binary_operation(other, "__imod__")

    @daskified(_DASKIFIED_VERBOSE)
    def __rmod__(self, other):
        """The binary arithmetic operation ``%`` with reflected
        operands.

        x.__rmod__(y) <==> y % x

        """
        return self._binary_operation(other, "__rmod__")

    @daskified(_DASKIFIED_VERBOSE)
    def __eq__(self, other):
        """The rich comparison operator ``==``

        x.__eq__(y) <==> x==y

        """
        return self._binary_operation(other, "__eq__")

    @daskified(_DASKIFIED_VERBOSE)
    def __ne__(self, other):
        """The rich comparison operator ``!=``

        x.__ne__(y) <==> x!=y

        """
        return self._binary_operation(other, "__ne__")

    @daskified(_DASKIFIED_VERBOSE)
    def __ge__(self, other):
        """The rich comparison operator ``>=``

        x.__ge__(y) <==> x>=y

        """
        return self._binary_operation(other, "__ge__")

    @daskified(_DASKIFIED_VERBOSE)
    def __gt__(self, other):
        """The rich comparison operator ``>``

        x.__gt__(y) <==> x>y

        """
        return self._binary_operation(other, "__gt__")

    @daskified(_DASKIFIED_VERBOSE)
    def __le__(self, other):
        """The rich comparison operator ``<=``

        x.__le__(y) <==> x<=y

        """
        return self._binary_operation(other, "__le__")

    @daskified(_DASKIFIED_VERBOSE)
    def __lt__(self, other):
        """The rich comparison operator ``<``

        x.__lt__(y) <==> x<y

        """
        return self._binary_operation(other, "__lt__")

    @daskified(_DASKIFIED_VERBOSE)
    def __and__(self, other):
        """The binary bitwise operation ``&``

        x.__and__(y) <==> x&y

        """
        return self._binary_operation(other, "__and__")

    @daskified(_DASKIFIED_VERBOSE)
    def __iand__(self, other):
        """The augmented bitwise assignment ``&=``

        x.__iand__(y) <==> x&=y

        """
        return self._binary_operation(other, "__iand__")

    @daskified(_DASKIFIED_VERBOSE)
    def __rand__(self, other):
        """The binary bitwise operation ``&`` with reflected operands.

        x.__rand__(y) <==> y&x

        """
        return self._binary_operation(other, "__rand__")

    @daskified(_DASKIFIED_VERBOSE)
    def __or__(self, other):
        """The binary bitwise operation ``|``

        x.__or__(y) <==> x|y

        """
        return self._binary_operation(other, "__or__")

    @daskified(_DASKIFIED_VERBOSE)
    def __ior__(self, other):
        """The augmented bitwise assignment ``|=``

        x.__ior__(y) <==> x|=y

        """
        return self._binary_operation(other, "__ior__")

    @daskified(_DASKIFIED_VERBOSE)
    def __ror__(self, other):
        """The binary bitwise operation ``|`` with reflected operands.

        x.__ror__(y) <==> y|x

        """
        return self._binary_operation(other, "__ror__")

    @daskified(_DASKIFIED_VERBOSE)
    def __xor__(self, other):
        """The binary bitwise operation ``^``

        x.__xor__(y) <==> x^y

        """
        return self._binary_operation(other, "__xor__")

    @daskified(_DASKIFIED_VERBOSE)
    def __ixor__(self, other):
        """The augmented bitwise assignment ``^=``

        x.__ixor__(y) <==> x^=y

        """
        return self._binary_operation(other, "__ixor__")

    @daskified(_DASKIFIED_VERBOSE)
    def __rxor__(self, other):
        """The binary bitwise operation ``^`` with reflected operands.

        x.__rxor__(y) <==> y^x

        """
        return self._binary_operation(other, "__rxor__")

    @daskified(_DASKIFIED_VERBOSE)
    def __lshift__(self, y):
        """The binary bitwise operation ``<<``

        x.__lshift__(y) <==> x<<y

        """
        return self._binary_operation(y, "__lshift__")

    @daskified(_DASKIFIED_VERBOSE)
    def __ilshift__(self, y):
        """The augmented bitwise assignment ``<<=``

        x.__ilshift__(y) <==> x<<=y

        """
        return self._binary_operation(y, "__ilshift__")

    @daskified(_DASKIFIED_VERBOSE)
    def __rlshift__(self, y):
        """The binary bitwise operation ``<<`` with reflected operands.

        x.__rlshift__(y) <==> y<<x

        """
        return self._binary_operation(y, "__rlshift__")

    @daskified(_DASKIFIED_VERBOSE)
    def __rshift__(self, y):
        """The binary bitwise operation ``>>``

        x.__lshift__(y) <==> x>>y

        """
        return self._binary_operation(y, "__rshift__")

    @daskified(_DASKIFIED_VERBOSE)
    def __irshift__(self, y):
        """The augmented bitwise assignment ``>>=``

        x.__irshift__(y) <==> x>>=y

        """
        return self._binary_operation(y, "__irshift__")

    @daskified(_DASKIFIED_VERBOSE)
    def __rrshift__(self, y):
        """The binary bitwise operation ``>>`` with reflected operands.

        x.__rrshift__(y) <==> y>>x

        """
        return self._binary_operation(y, "__rrshift__")

    @daskified(_DASKIFIED_VERBOSE)
    def __abs__(self):
        """The unary arithmetic operation ``abs``

        x.__abs__() <==> abs(x)

        """
        return self._unary_operation("__abs__")

    @daskified(_DASKIFIED_VERBOSE)
    def __neg__(self):
        """The unary arithmetic operation ``-``

        x.__neg__() <==> -x

        """
        return self._unary_operation("__neg__")

    @daskified(_DASKIFIED_VERBOSE)
    def __invert__(self):
        """The unary bitwise operation ``~``

        x.__invert__() <==> ~x

        """
        return self._unary_operation("__invert__")

    @daskified(_DASKIFIED_VERBOSE)
    def __pos__(self):
        """The unary arithmetic operation ``+``

        x.__pos__() <==> +x

        """
        return self._unary_operation("__pos__")

    # ----------------------------------------------------------------
    # Private attributes
    # ----------------------------------------------------------------
    @property
    def _Units(self):
        """Storage for the units.

        The units are stored in a `Units` object, and reflect the
        units of the (yet to be computed) elements of the underlying
        data.

        .. warning:: Assigning to `_Units` does *not* trigger a units
                     conversion of the underlying data
                     values. Therefore assigning to `_Units` should
                     only be done in cases when it is known that the
                     intrinsic units represented by the data values
                     are inconsistent with the existing value of
                     `_Units`. Before assigning to `_Units`, first
                     consider if assigning to `Units`, or calling the
                     `override_units` or `override_calendar` method is
                     a more appropriate course of action, and use one
                     of those if possible.

        """
        return self._custom["_Units"]

    @_Units.setter
    def _Units(self, value):
        self._custom["_Units"] = value

    @_Units.deleter
    def _Units(self):
        self._custom["_Units"] = _units_None

    @property
    def _cyclic(self):
        """Storage for axis cyclicity.

        Contains a `set` that identifies which axes are cyclic (and
        therefore allow cyclic slicing). The set contains a subset of
        the axis identifiers defined by the `_axes` attribute.

        .. warning:: Never change the value of the `_cyclic` attribute
                     in-place.

        .. note:: When an axis identifier is removed from the `_axes`
                  attribute then it is automatically also removed from
                  the `_cyclic` attribute.

        """
        return self._custom["_cyclic"]

    @_cyclic.setter
    def _cyclic(self, value):
        self._custom["_cyclic"] = value

    @_cyclic.deleter
    def _cyclic(self):
        self._custom["_cyclic"] = _empty_set

    @property
    @daskified(_DASKIFIED_VERBOSE)
    def _axes(self):
        """Storage for the axis identifiers.

        Contains a `tuple` of identifiers, one for each array axis.

        .. note:: When the axis identifiers are reset, then any axis
                  identifier named by the `_cyclic` attribute which is
                  not in the new `_axes` set is automatically removed
                  from the `_cyclic` attribute.

        """
        return self._custom["_axes"]

    @_axes.setter
    def _axes(self, value):
        self._custom["_axes"] = tuple(value)

        # Remove cyclic axes that are not in the new axes
        cyclic = self._cyclic
        if cyclic:
            # Never change the value of the _cyclic attribute in-place
            self._cyclic = cyclic.intersection(value)

    # ----------------------------------------------------------------
    # Dask attributes
    # ----------------------------------------------------------------
    @property
    def chunks(self):
        """The chunk sizes for each dimension.

        **Examples**

        >>> d = cf.Data.ones((4, 5), chunks=(2, 4))
        >>> d.chunks
        ((2, 2), (4, 1))

        """
        return self.to_dask_array().chunks

    @property
    def force_compute(self):
        """TODODASKDOCS See also config settings."""
        return self._custom.get("force_compute", False)

    @force_compute.setter
    def force_compute(self, value):
        self._custom["force_compute"] = bool(value)

    # ----------------------------------------------------------------
    # Attributes
    # ----------------------------------------------------------------
    @property
    @daskified(_DASKIFIED_VERBOSE)
    def Units(self):
        """The `cf.Units` object containing the units of the data array.

        Can be set to any units equivalent to the existing units.

        .. seealso `override_units`, `override_calendar`

        **Examples**

        >>> d = cf.Data([1, 2, 3], units='m')
        >>> d.Units
        <Units: m>
        >>> d.Units = cf.Units('kilmetres')
        >>> d.Units
        <Units: kilmetres>
        >>> d.Units = cf.Units('km')
        >>> d.Units
        <Units: km>

        """
        return self._Units

    @Units.setter
    def Units(self, value):
        try:
            old_units = self._Units
        except KeyError:
            pass
        else:
            if old_units and not old_units.equivalent(value):
                raise ValueError(
                    f"Can't set Units to {value!r} that are not "
                    f"equivalent to the current units {old_units!r}. "
                    "Consider using the override_units method instead."
                )

            if not old_units or self.Units.equals(value):
                self._Units = value
                return

        dtype = self.dtype
        if dtype.kind in "iu":
            if dtype.char in "iI":
                dtype = _dtype_float32
            else:
                dtype = _dtype_float

        dx = self.to_dask_array()
        dx = dx.map_blocks(
            partial(cf_units, from_units=old_units, to_units=value),
            dtype=dtype,
        )
        self._set_dask(dx)

        self._Units = value

    @Units.deleter
    def Units(self):
        raise ValueError(
            "Can't delete the Units attribute. "
            "Consider using the override_units method instead."
        )

    @property
    @daskified(_DASKIFIED_VERBOSE)
    def data(self):
        """The data as an object identity.

        **Examples**

        >>> d = cf.Data([1, 2], 'm')
        >>> d.data is d
        True

        """
        return self

    @property
    @daskified(_DASKIFIED_VERBOSE)
    def dtype(self):
        """The `numpy` data-type of the data.

        **Examples**

        TODODASKDOCS
        >>> d = cf.Data([0.5, 1.5, 2.5])
        >>> d.dtype
        dtype(float64')
        >>> type(d.dtype)
        <type 'numpy.dtype'>

        >>> d = cf.Data([0.5, 1.5, 2.5])
        >>> import numpy
        >>> d.dtype = numpy.dtype(int)
        >>> print(d.array)
        [0 1 2]
        >>> d.dtype = bool
        >>> print(d.array)
        [False  True  True]
        >>> d.dtype = 'float64'
        >>> print(d.array)
        [ 0.  1.  1.]

        >>> d = cf.Data([0.5, 1.5, 2.5])
        >>> d.dtype = int
        >>> d.dtype = bool
        >>> d.dtype = float
        >>> print(d.array)
        [ 0.5  1.5  2.5]

        """
        dx = self.to_dask_array()
        return dx.dtype

    @dtype.setter
    def dtype(self, value):
        dx = self.to_dask_array()

        # Only change the datatype if it's different to that of the
        # dask array
        if dx.dtype != value:
            dx = dx.astype(value)
            self._set_dask(dx)

    @property
    @daskified(_DASKIFIED_VERBOSE)
    def fill_value(self):
        """The data array missing data value.

        If set to `None` then the default `numpy` fill value appropriate to
        the data array's data-type will be used.

        Deleting this attribute is equivalent to setting it to None, so
        this attribute is guaranteed to always exist.

        **Examples**

        >>> d.fill_value = 9999.0
        >>> d.fill_value
        9999.0
        >>> del d.fill_value
        >>> d.fill_value
        None

        """
        return self.get_fill_value(None)

    @fill_value.setter
    def fill_value(self, value):
        self.set_fill_value(value)

    @fill_value.deleter
    def fill_value(self):
        self.del_fill_value(None)

    @property
    @daskified(_DASKIFIED_VERBOSE)
    def hardmask(self):
        """Hardness of the mask.

        If the `hardmask` attribute is `True`, i.e. there is a hard
        mask, then unmasking an entry will silently not occur. This is
        the default, and prevents overwriting the mask.

        If the `hardmask` attribute is `False`, i.e. there is a soft
        mask, then masked entries may be overwritten with non-missing
        values.

        .. note:: Setting the `hardmask` attribute does not
                  immediately change the mask hardness, rather its
                  value indicates to other methods (such as `where`,
                  `transpose`, etc.) whether or not the mask needs
                  hardening or softening prior to an operation being
                  defined, and those methods will reset the mask
                  hardness if required.

                  By contrast, the `harden_mask` and `soften_mask`
                  methods immediately reset the mask hardness of the
                  underlying `dask` array, and also set the value of
                  the `hardmask` attribute.

        .. seealso:: `harden_mask`, `soften_mask`, `to_dask_array`,
                     `where`, `__setitem__`

        **Examples**

        >>> d = cf.Data([1, 2, 3])
        >>> d.hardmask
        True
        >>> d[0] = cf.masked
        >>> print(d.array)
        [-- 2 3]
        >>> d[...] = 999
        >>> print(d.array)
        [-- 999 999]
        >>> d.hardmask = False
        >>> d.hardmask
        False
        >>> d[...] = -1
        >>> print(d.array)
        [-1 -1 -1]

        """
        return self._custom.get("hardmask", _DEFAULT_HARDMASK)

    @hardmask.setter
    def hardmask(self, value):
        self._custom["hardmask"] = value

    @property
    @daskified(_DASKIFIED_VERBOSE)
    def is_masked(self):
        """True if the data array has any masked values.

        **Performance**

        `is_masked` causes all delayed operations to be executed.

        **Examples**

        >>> d = cf.Data([[1, 2, 3], [4, 5, 6]])
        >>> print(d.is_masked)
        False
        >>> d[0, ...] = cf.masked
        >>> d.is_masked
        True

        """

        def is_masked(a):
            out = np.ma.is_masked(a)
            return np.array(out).reshape((1,) * a.ndim)

        dx = self.to_dask_array()

        out_ind = tuple(range(dx.ndim))
        dx_ind = out_ind

        dx = da.blockwise(
            is_masked,
            out_ind,
            dx,
            dx_ind,
            adjust_chunks={i: 1 for i in out_ind},
            dtype=bool,
        )

        return bool(dx.any())

    @property
    @daskified(_DASKIFIED_VERBOSE)
    def nbytes(self):
        """Total number of bytes consumed by the elements of the array.

        Does not include bytes consumed by the array mask

        **Performance**

        If the number of bytes is unknown then it is calculated
        immediately by executing all delayed operations.

        **Examples**

        >>> d = cf.Data([[1, 1.5, 2]])
        >>> d.dtype
        dtype('float64')
        >>> d.size, d.dtype.itemsize
        (3, 8)
        >>> d.nbytes
        24
        >>> d[0] = cf.masked
        >>> print(d.array)
        [[-- 1.5 2.0]]
        >>> d.nbytes
        24

        """
        dx = self.to_dask_array()
        if math.isnan(dx.size):
            logger.debug("Computing data nbytes: Performance may be degraded")
            dx.compute_chunk_sizes()

        return dx.nbytes

    @property
    @daskified(_DASKIFIED_VERBOSE)
    def ndim(self):
        """Number of dimensions in the data array.

        **Examples**

        >>> d = cf.Data([[1, 2, 3], [4, 5, 6]])
        >>> d.ndim
        2

        >>> d = cf.Data([[1, 2, 3]])
        >>> d.ndim
        2

        >>> d = cf.Data([[3]])
        >>> d.ndim
        2

        >>> d = cf.Data([3])
        >>> d.ndim
        1

        >>> d = cf.Data(3)
        >>> d.ndim
        0

        """
        dx = self.to_dask_array()
        return dx.ndim

    @property
    @daskified(_DASKIFIED_VERBOSE)
    def shape(self):
        """Tuple of the data array's dimension sizes.

        **Performance**

        If the shape of the data is unknown then it is calculated
        immediately by executing all delayed operations.

        **Examples**

        >>> d = cf.Data([[1, 2, 3], [4, 5, 6]])
        >>> d.shape
        (2, 3)

        >>> d = cf.Data([[1, 2, 3]])
        >>> d.shape
        (1, 3)

        >>> d = cf.Data([[3]])
        >>> d.shape
        (1, 1)

        >>> d = cf.Data(3)
        >>> d.shape
        ()

        """
        dx = self.to_dask_array()
        if math.isnan(dx.size):
            logger.debug("Computing data shape: Performance may be degraded")
            dx.compute_chunk_sizes()

        return dx.shape

    @property
    @daskified(_DASKIFIED_VERBOSE)
    def size(self):
        """Number of elements in the data array.

        **Performance**

        If the size of the data is unknown then it is calculated
        immediately by executing all delayed operations.

        **Examples**

        >>> d = cf.Data([[1, 2, 3], [4, 5, 6]])
        >>> d.size
        6

        >>> d = cf.Data([[1, 2, 3]])
        >>> d.size
        3

        >>> d = cf.Data([[3]])
        >>> d.size
        1

        >>> d = cf.Data([3])
        >>> d.size
        1

        >>> d = cf.Data(3)
        >>> d.size
        1

        """
        dx = self.to_dask_array()
        size = dx.size
        if math.isnan(size):
            logger.debug("Computing data size: Performance may be degraded")
            dx.compute_chunk_sizes()
            size = dx.size

        return size

    @property
    @daskified(_DASKIFIED_VERBOSE)
    def array(self):
        """A numpy array copy of the data.

        In-place changes to the returned numpy array do not affect the
        underlying dask array.

        The returned numpy array has the same mask hardness and fill
        values as the data.

        Compare with `compute`.

        **Performance**

        `array` causes all delayed operations to be computed.

        .. seealso:: `datetime_array`, `compute`, `persist`

        **Examples**

        >>> d = cf.Data([1, 2, 3.0], 'km')
        >>> a = d.array
        >>> isinstance(a, numpy.ndarray)
        True
        >>> print(a)
        [ 1.  2.  3.]
        >>> d[0] = -99
        >>> print(a[0])
        1.0
        >>> a[0] = 88
        >>> print(d[0])
        -99.0 km

        >>> d = cf.Data('2000-12-1', units='days since 1999-12-1')
        >>> print(d.array)
        366
        >>> print(d.datetime_array)
        2000-12-01 00:00:00

        """
        return self.compute().copy()

    @property
    @daskified(_DASKIFIED_VERBOSE)
    def datetime_array(self):
        """An independent numpy array of date-time objects.

        Only applicable to data arrays with reference time units.

        If the calendar has not been set then the CF default calendar will
        be used and the units will be updated accordingly.

        The data-type of the data array is unchanged.

        .. seealso:: `array`, `compute`, `persist`

        **Performance**

        `datetime_array` causes all delayed operations to be computed.

        **Examples**

        """
        units = self.Units

        if not units.isreftime:
            raise ValueError(
                f"Can't create date-time array from units {self.Units!r}"
            )

        if getattr(units, "calendar", None) == "none":
            raise ValueError(
                f"Can't create date-time array from units {self.Units!r} "
                "because calendar is 'none'"
            )

        units, reftime = units.units.split(" since ")

        # Convert months and years to days, because cftime won't work
        # otherwise.
        if units in ("months", "month"):
            d = self * _month_length
            d.override_units(
                Units(
                    f"days since {reftime}",
                    calendar=getattr(units, "calendar", None),
                ),
                inplace=True,
            )
        elif units in ("years", "year", "yr"):
            d = self * _year_length
            d.override_units(
                Units(
                    f"days since {reftime}",
                    calendar=getattr(units, "calendar", None),
                ),
                inplace=True,
            )
        else:
            d = self

        dx = d.to_dask_array()
        dx = convert_to_datetime(dx, d.Units)

        a = dx.compute()

        if np.ma.isMA(a):
            if self.hardmask:
                a.harden_mask()
            else:
                a.soften_mask()

            a.set_fill_value(self.fill_value)

        return a

    @property
    @daskified(_DASKIFIED_VERBOSE)
    def mask(self):
        """The Boolean missing data mask of the data array.

        The Boolean mask has True where the data array has missing data
        and False otherwise.

        :Returns:

            `Data`

        **Examples**

        >>> d.shape
        (12, 73, 96)
        >>> m = d.mask
        >>> m.dtype
        dtype('bool')
        >>> m.shape
        (12, 73, 96)

        """
        mask_data_obj = self.copy(array=False)

        dx = self.to_dask_array()
        mask = da.ma.getmaskarray(dx)

        mask_data_obj._set_dask(mask)
        mask_data_obj.override_units(_units_None, inplace=True)
        mask_data_obj.hardmask = _DEFAULT_HARDMASK

        return mask_data_obj

    # `arctan2`, AT2 seealso
    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def arctan(self, inplace=False):
        """Take the trigonometric inverse tangent of the data element-
        wise.

        Units are ignored in the calculation. The result has units of radians.

        .. versionadded:: 3.0.7

        .. seealso:: `tan`, `arcsin`, `arccos`, `arctanh`

        :Parameters:

            {{inplace: `bool`, optional}}

        :Returns:

            `Data` or `None`

        **Examples**

        >>> print(d.array)
        [[0.5 0.7]
         [0.9 1.1]]
        >>> e = d.arctan()
        >>> e.Units
        <Units: radians>
        >>> print(e.array)
        [[0.46364761 0.61072596]
         [0.7328151  0.83298127]]

        >>> print(d.array)
        [1.2 1.0 0.8 0.6 --]
        >>> d.arctan(inplace=True)
        >>> print(d.array)
        [0.8760580505981934 0.7853981633974483 0.6747409422235527
         0.5404195002705842 --]

        """
        d = _inplace_enabled_define_and_cleanup(self)

        dx = d.to_dask_array()
        d._set_dask(da.arctan(dx))

        d.override_units(_units_radians, inplace=True)

        return d

    # AT2
    #
    #    @classmethod
    #    def arctan2(cls, y, x):
    #        '''Take the "two-argument" trigonometric inverse tangent
    #    element-wise for `y`/`x`.
    #
    #    Explicitly this returns, for all corresponding elements, the angle
    #    between the positive `x` axis and the line to the point (`x`, `y`),
    #    where the signs of both `x` and `y` are taken into account to
    #    determine the quadrant. Such knowledge of the signs of `x` and `y`
    #    are lost when the quotient is input to the standard "one-argument"
    #    `arctan` function, such that use of `arctan` leaves the quadrant
    #    ambiguous. `arctan2` may therefore be preferred.
    #
    #    Units are ignored in the calculation. The result has units of radians.
    #
    #    .. versionadded:: 3.2.0
    #
    #    .. seealso:: `arctan`, `tan`
    #
    #    :Parameters:
    #
    #        y: `Data`
    #            The data array to provide the numerator elements, corresponding
    #            to the `y` coordinates in the `arctan2` definition.
    #
    #        x: `Data`
    #            The data array to provide the denominator elements,
    #            corresponding to the `x` coordinates in the `arctan2`
    #            definition.
    #
    #    :Returns:
    #
    #        `Data`
    #
    #    **Examples**
    #
    #        '''
    #        return cls(numpy_arctan2(y, x), units=_units_radians)

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def arctanh(self, inplace=False):
        """Take the inverse hyperbolic tangent of the data element-wise.

        Units are ignored in the calculation. The result has units of radians.

        .. versionadded:: 3.2.0

        .. seealso::  `tanh`, `arcsinh`, `arccosh`, `arctan`

        :Parameters:

            {{inplace: `bool`, optional}}

        :Returns:

            `Data` or `None`

        **Examples**

        >>> print(d.array)
        [[0.5 0.7]
         [0.9 1.1]]
        >>> e = d.arctanh()
        >>> e.Units
        <Units: radians>
        >>> print(e.array)
        [[0.54930614 0.86730053]
         [1.47221949        nan]]

        >>> print(d.array)
        [1.2 1.0 0.8 0.6 --]
        >>> d.arctanh(inplace=True)
        >>> print(d.array)
        [nan inf 1.0986122886681098 0.6931471805599453 --]
        >>> d.masked_invalid(inplace=True)
        >>> print(d.array)
        [-- -- 1.0986122886681098 0.6931471805599453 --]

        """
        d = _inplace_enabled_define_and_cleanup(self)

        # Data.func is used instead of the Dask built-in in this case because
        # arctanh has a restricted domain therefore it is necessary to use our
        # custom logic implemented via the `preserve_invalid` keyword to func.
        d.func(
            np.arctanh,
            units=_units_radians,
            inplace=True,
            preserve_invalid=True,
        )

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def arcsin(self, inplace=False):
        """Take the trigonometric inverse sine of the data element-wise.

        Units are ignored in the calculation. The result has units of radians.

        .. versionadded:: 3.2.0

        .. seealso::  `sin`, `arccos`, `arctan`, `arcsinh`

        :Parameters:

            {{inplace: `bool`, optional}}

        :Returns:

            `Data` or `None`

        **Examples**

        >>> print(d.array)
        [[0.5 0.7]
         [0.9 1.1]]
        >>> e = d.arcsin()
        >>> e.Units
        <Units: radians>
        >>> print(e.array)
        [[0.52359878 0.7753975 ]
         [1.11976951        nan]]

        >>> print(d.array)
        [1.2 1.0 0.8 0.6 --]
        >>> d.arcsin(inplace=True)
        >>> print(d.array)
        [nan 1.5707963267948966 0.9272952180016123 0.6435011087932844 --]
        >>> d.masked_invalid(inplace=True)
        >>> print(d.array)
        [-- 1.5707963267948966 0.9272952180016123 0.6435011087932844 --]

        """
        d = _inplace_enabled_define_and_cleanup(self)

        # Data.func is used instead of the Dask built-in in this case because
        # arcsin has a restricted domain therefore it is necessary to use our
        # custom logic implemented via the `preserve_invalid` keyword to func.
        d.func(
            np.arcsin,
            units=_units_radians,
            inplace=True,
            preserve_invalid=True,
        )

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def arcsinh(self, inplace=False):
        """Take the inverse hyperbolic sine of the data element-wise.

        Units are ignored in the calculation. The result has units of radians.

        .. versionadded:: 3.1.0

        .. seealso:: `sinh`, `arccosh`, `arctanh`, `arcsin`

        :Parameters:

            {{inplace: `bool`, optional}}

        :Returns:

            `Data` or `None`

        **Examples**

        >>> print(d.array)
        [[0.5 0.7]
         [0.9 1.1]]
        >>> e = d.arcsinh()
        >>> e.Units
        <Units: radians>
        >>> print(e.array)
        [[0.48121183 0.65266657]
         [0.80886694 0.95034693]]

        >>> print(d.array)
        [1.2 1.0 0.8 0.6 --]
        >>> d.arcsinh(inplace=True)
        >>> print(d.array)
        [1.015973134179692 0.881373587019543 0.732668256045411 0.5688248987322475
         --]

        """
        d = _inplace_enabled_define_and_cleanup(self)

        dx = d.to_dask_array()
        d._set_dask(da.arcsinh(dx))

        d.override_units(_units_radians, inplace=True)

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def arccos(self, inplace=False):
        """Take the trigonometric inverse cosine of the data element-
        wise.

        Units are ignored in the calculation. The result has units of radians.

        .. versionadded:: 3.2.0

        .. seealso:: `cos`, `arcsin`, `arctan`, `arccosh`

        :Parameters:

            {{inplace: `bool`, optional}}

        :Returns:

            `Data` or `None`

        **Examples**

        >>> print(d.array)
        [[0.5 0.7]
         [0.9 1.1]]
        >>> e = d.arccos()
        >>> e.Units
        <Units: radians>
        >>> print(e.array)
        [[1.04719755 0.79539883]
         [0.45102681        nan]]

        >>> print(d.array)
        [1.2 1.0 0.8 0.6 --]
        >>> d.arccos(inplace=True)
        >>> print(d.array)
        [nan 0.0 0.6435011087932843 0.9272952180016123 --]
        >>> d.masked_invalid(inplace=True)
        >>> print(d.array)
        [-- 0.0 0.6435011087932843 0.9272952180016123 --]

        """
        d = _inplace_enabled_define_and_cleanup(self)

        # Data.func is used instead of the Dask built-in in this case because
        # arccos has a restricted domain therefore it is necessary to use our
        # custom logic implemented via the `preserve_invalid` keyword to func.
        d.func(
            np.arccos,
            units=_units_radians,
            inplace=True,
            preserve_invalid=True,
        )

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def arccosh(self, inplace=False):
        """Take the inverse hyperbolic cosine of the data element-wise.

        Units are ignored in the calculation. The result has units of radians.

        .. versionadded:: 3.2.0

        .. seealso::  `cosh`, `arcsinh`, `arctanh`, `arccos`

        :Parameters:

            {{inplace: `bool`, optional}}

        :Returns:

            `Data` or `None`

        **Examples**

        >>> print(d.array)
        [[0.5 0.7]
         [0.9 1.1]]
        >>> e = d.arccosh()
        >>> e.Units
        <Units: radians>
        >>> print(e.array)
        [[       nan        nan]
         [       nan 0.44356825]]

        >>> print(d.array)
        [1.2 1.0 0.8 0.6 --]
        >>> d.arccosh(inplace=True)
        >>> print(d.array)
        [0.6223625037147786 0.0 nan nan --]
        >>> d.masked_invalid(inplace=True)
        >>> print(d.array)
        [0.6223625037147786 0.0 -- -- --]

        """
        d = _inplace_enabled_define_and_cleanup(self)

        # Data.func is used instead of the Dask built-in in this case because
        # arccosh has a restricted domain therefore it is necessary to use our
        # custom logic implemented via the `preserve_invalid` keyword to func.
        d.func(
            np.arccosh,
            units=_units_radians,
            inplace=True,
            preserve_invalid=True,
        )

        return d

    @daskified(_DASKIFIED_VERBOSE)
    def all(self, axis=None, keepdims=True, split_every=None):
        """Test whether all data array elements evaluate to True.

        .. seealso:: `allclose`, `any`, `isclose`

        :Parameters:

            axis: (sequence of) `int`, optional
                Axis or axes along which a logical AND reduction is
                performed. The default (`None`) is to perform a
                logical AND over all the dimensions of the input
                array. *axis* may be negative, in which case it counts
                from the last to the first axis.

            {{collapse keepdims: `bool`, optional}}

            {{split_every: `int` or `dict`, optional}}

        :Returns:

            `Data`
                Whether or not all data array elements evaluate to True.

        **Examples**

        >>> d = cf.Data([[1, 2], [3, 4]])
        >>> d.all()
        <CF Data(1, 1): [[True]]>
        >>> d.all(keepdims=False)
        <CF Data(1, 1): True>
        >>> d.all(axis=0)
        <CF Data(1, 2): [[True, True]]>
        >>> d.all(axis=1)
        <CF Data(2, 1): [[True, True]]>
        >>> d.all(axis=())
        <CF Data(2, 2): [[True, ..., True]]>

        >>> d[0] = cf.masked
        >>> d[1, 0] = 0
        >>> print(d.array)
        [[-- --]
         [0 4]]
        >>> d.all(axis=0)
        <CF Data(1, 2): [[False, True]]>
        >>> d.all(axis=1)
        <CF Data(2, 1): [[--, False]]>

        >>> d[...] = cf.masked
        >>> d.all()
        <CF Data(1, 1): [[--]]>
        >>> bool(d.all())
        True
        >>> bool(d.all(keepdims=False))
        False

        """
        d = self.copy(array=False)
        dx = self.to_dask_array()
        dx = da.all(dx, axis=axis, keepdims=keepdims, split_every=split_every)
        d._set_dask(dx)
        d.hardmask = _DEFAULT_HARDMASK
        d.override_units(_units_None, inplace=True)
        return d

    @daskified(_DASKIFIED_VERBOSE)
    def allclose(self, y, rtol=None, atol=None):
        """Whether an array is element-wise equal within a tolerance.

        Return True if the data is broadcastable to array *y* and
        element-wise equal within a tolerance.

        {{equals tolerance}}

        .. seealso:: `all`, `any`, `isclose`

        :Parameters:

            y: data_like
                The data to compare.

            {{rtol: number, optional}}

            {{atol: number, optional}}

        :Returns:

            `Data`
                A scalar boolean array that is `True` if the two arrays
                are equal within the given tolerance, or `False`
                otherwise.

        **Examples**

        >>> d = cf.Data([1000, 2500], 'metre')
        >>> e = cf.Data([1, 2.5], 'km')
        >>> bool(d.allclose(e))
        True

        >>> d = cf.Data(['ab', 'cdef'])
        >>> bool(d.allclose([[['ab', 'cdef']]]))
        True

        >>> d = cf.Data([[1000, 2500], [1000, 2500]], 'metre')
        >>> e = cf.Data([1, 2.5], 'km')
        >>> bool(d.allclose(e))
        True

        >>> d = cf.Data([1, 1, 1], 's')
        >>> bool(d.allclose(1))
        True

        """
        return self.isclose(y, rtol=rtol, atol=atol).all()

    def any(self, axis=None, keepdims=True, split_every=None):
        """Test whether any data array elements evaluate to True.

        .. seealso:: `all`, `allclose`, `isclose`

        :Parameters:

            axis: (sequence of) `int`, optional
                Axis or axes along which a logical OR reduction is
                performed. The default (`None`) is to perform a
                logical OR over all the dimensions of the input
                array. *axis* may be negative, in which case it counts
                from the last to the first axis.

            {{collapse keepdims: `bool`, optional}}

            {{split_every: `int` or `dict`, optional}}

        :Returns:

            `Data`
                Whether or any data array elements evaluate to True.

        **Examples**

        >>> d = cf.Data([[0, 2], [0, 4]])
        >>> d.any()
        <CF Data(1, 1): [[True]]>
        >>> d.any(keepdims=False)
        <CF Data(1, 1): True>
        >>> d.any(axis=0)
        <CF Data(1, 2): [[False, True]]>
        >>> d.any(axis=1)
        <CF Data(2, 1): [[True, True]]>
        >>> d.any(axis=())
        <CF Data(2, 2): [[False, ..., True]]>

        >>> d[0] = cf.masked
        >>> print(d.array)
        [[-- --]
         [0 4]]
        >>> d.any(axis=0)
        <CF Data(1, 2): [[False, True]]>
        >>> d.any(axis=1)
        <CF Data(2, 1): [[--, True]]>

        >>> d[...] = cf.masked
        >>> d.any()
        <CF Data(1, 1): [[--]]>
        >>> bool(d.any())
        False
        >>> bool(d.any(keepdims=False))
        False

        """
        d = self.copy(array=False)
        dx = self.to_dask_array()
        dx = da.any(dx, axis=axis, keepdims=keepdims, split_every=split_every)
        d._set_dask(dx)
        d.hardmask = _DEFAULT_HARDMASK
        d.override_units(_units_None, inplace=True)
        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def apply_masking(
        self,
        fill_values=None,
        valid_min=None,
        valid_max=None,
        valid_range=None,
        inplace=False,
    ):
        """Apply masking.

        Masking is applied according to the values of the keyword
        parameters.

        Elements that are already masked remain so.

        .. versionadded:: 3.4.0

        .. seealso:: `get_fill_value`, `hardmask`, `mask`, `where`

        :Parameters:

            fill_values: `bool` or sequence of scalars, optional
                Specify values that will be set to missing data. Data
                elements exactly equal to any of the values are set to
                missing data.

                If True then the value returned by the
                `get_fill_value` method, if such a value exists, is
                used.

                Zero or more values may be provided in a sequence of
                scalars.

                *Parameter example:*
                  Specify a fill value of 999: ``fill_values=[999]``

                *Parameter example:*
                  Specify fill values of 999 and -1.0e30:
                  ``fill_values=[999, -1.0e30]``

                *Parameter example:*
                  Use the fill value already set for the data:
                  ``fill_values=True``

                *Parameter example:*
                  Use no fill values: ``fill_values=False`` or
                  ``fill_value=[]``

            valid_min: number, optional
                A scalar specifying the minimum valid value. Data
                elements strictly less than this number will be set to
                missing data.

            valid_max: number, optional
                A scalar specifying the maximum valid value. Data
                elements strictly greater than this number will be set
                to missing data.

            valid_range: (number, number), optional
                A vector of two numbers specifying the minimum and
                maximum valid values, equivalent to specifying values
                for both *valid_min* and *valid_max* parameters. The
                *valid_range* parameter must not be set if either
                *valid_min* or *valid_max* is defined.

                *Parameter example:*
                  ``valid_range=[-999, 10000]`` is equivalent to setting
                  ``valid_min=-999, valid_max=10000``

            {{inplace: `bool`, optional}}

        :Returns:

            `Data` or `None`
                The data with masked values. If the operation was in-place
                then `None` is returned.

        **Examples**

        >>> import numpy
        >>> d = cf.Data(numpy.arange(12).reshape(3, 4), 'm')
        >>> d[1, 1] = cf.masked
        >>> print(d.array)
        [[0 1 2 3]
         [4 -- 6 7]
         [8 9 10 11]]
        >>> print(d.apply_masking().array)
        [[0 1 2 3]
         [4 -- 6 7]
         [8 9 10 11]]
        >>> print(d.apply_masking(fill_values=[0]).array)
        [[-- 1 2 3]
         [4 -- 6 7]
         [8 9 10 11]]
        >>> print(d.apply_masking(fill_values=[0, 11]).array)
        [[-- 1 2 3]
         [4 -- 6 7]
         [8 9 10 --]]
        >>> print(d.apply_masking(valid_min=3).array)
        [[-- -- -- 3]
         [4 -- 6 7]
         [8 9 10 11]]
        >>> print(d.apply_masking(valid_max=6).array)
        [[0 1 2 3]
         [4 -- 6 --]
         [-- -- -- --]]
        >>> print(d.apply_masking(valid_range=[2, 8]).array)
        [[-- -- 2 3]
         [4 -- 6 7]
         [8 -- -- --]]
        >>> d.set_fill_value(7)
        >>> print(d.apply_masking(fill_values=True).array)
        [[0 1 2 3]
         [4 -- 6 --]
         [8 9 10 11]]
        >>> print(d.apply_masking(fill_values=True,
        ...                       valid_range=[2, 8]).array)
        [[-- -- 2 3]
         [4 -- 6 --]
         [8 -- -- --]]

        """
        # Parse valid_range
        if valid_range is not None:
            if valid_min is not None or valid_max is not None:
                raise ValueError(
                    "Can't set 'valid_range' parameter with either the "
                    "'valid_min' nor 'valid_max' parameters"
                )

            try:
                if len(valid_range) != 2:
                    raise ValueError(
                        "'valid_range' parameter must be a vector of "
                        "two elements"
                    )
            except TypeError:
                raise ValueError(
                    "'valid_range' parameter must be a vector of "
                    "two elements"
                )

            valid_min, valid_max = valid_range

        # Parse fill_values
        if fill_values is None:
            fill_values = False

        if isinstance(fill_values, bool):
            if fill_values:
                fill_value = self.get_fill_value(None)
                if fill_value is not None:
                    fill_values = (fill_value,)
                else:
                    fill_values = ()
            else:
                fill_values = ()
        else:
            try:
                iter(fill_values)
            except TypeError:
                raise TypeError(
                    "'fill_values' parameter must be a sequence or "
                    f"of type bool. Got type {type(fill_values)}"
                )
            else:
                if isinstance(fill_values, str):
                    raise TypeError(
                        "'fill_values' parameter must be a sequence or "
                        f"of type bool. Got type {type(fill_values)}"
                    )

        d = _inplace_enabled_define_and_cleanup(self)

        dx = self.to_dask_array()

        mask = None
        if fill_values:
            mask = dx == fill_values[0]

            for fill_value in fill_values[1:]:
                mask |= dx == fill_value

        if valid_min is not None:
            if mask is None:
                mask = dx < valid_min
            else:
                mask |= dx < valid_min

        if valid_max is not None:
            if mask is None:
                mask = dx > valid_max
            else:
                mask |= dx > valid_max

        if mask is not None:
            dx = da.ma.masked_where(mask, dx)

        d._set_dask(dx)

        return d

    def argmax(self, axis=None, unravel=False):
        """Return the indices of the maximum values along an axis.

        If no axis is specified then the returned index locates the
        maximum of the whole data.

        In case of multiple occurrences of the maximum values, the
        indices corresponding to the first occurrence are returned.

        **Performance**

        If the data index is returned as a `tuple` (see the *unravel*
        parameter) then all delayed operations are computed.

        :Parameters:

            axis: `int`, optional
                The specified axis over which to locate the maximum
                values. By default the maximum over the flattened data
                is located.

            unravel: `bool`, optional
                If True then when locating the maximum over the whole
                data, return the location as an index for each axis as
                a `tuple`. By default an index to the flattened array
                is returned in this case. Ignored if locating the
                maxima over a subset of the axes.

        :Returns:

            `Data` or `tuple`
                The location of the maximum, or maxima.

        **Examples**

        >>> d = cf.Data(np.arange(6).reshape(2, 3))
        >>> print(d.array)
        [[0 1 2]
         [3 4 5]]
        >>> a = d.argmax()
        >>> a
        <CF Data(): 5>
        >>> a.array
        5

        >>> index = d.argmax(unravel=True)
        >>> index
        (1, 2)
        >>> d[index]
        <CF Data(1, 1): [[5]]>

        >>> d.argmax(axis=0)
        <CF Data(3): [1, 1, 1]>
        >>> d.argmax(axis=1)
        <CF Data(2): [2, 2]>

        Only the location of the first occurrence is returned:

        >>> d = cf.Data([0, 4, 2, 3, 4])
        >>> d.argmax()
        <CF Data(): 1>

        >>> d = cf.Data(np.arange(6).reshape(2, 3))
        >>> d[1, 1] = 5
        >>> print(d.array)
        [[0 1 2]
         [3 5 5]]
        >>> d.argmax(1)
        <CF Data(2): [2, 1]>

        """
        dx = self.to_dask_array()
        a = dx.argmax(axis=axis)

        if unravel and (axis is None or self.ndim <= 1):
            # Return a multidimensional index tuple
            return tuple(np.array(da.unravel_index(a, self.shape)))

        return type(self)(a)

    @daskified(_DASKIFIED_VERBOSE)
    def get_data(self, default=ValueError(), _units=None, _fill_value=None):
        """Returns the data.

        .. versionadded:: 3.0.0

        :Returns:

                `Data`

        """
        return self

    @daskified(_DASKIFIED_VERBOSE)
    def get_units(self, default=ValueError()):
        """Return the units.

        .. seealso:: `del_units`, `set_units`

        :Parameters:

            default: optional
                Return the value of the *default* parameter if the units
                have not been set. If set to an `Exception` instance then
                it will be raised instead.

        :Returns:

                The units.

        **Examples**

        >>> d.set_units('metres')
        >>> d.get_units()
        'metres'
        >>> d.del_units()
        >>> d.get_units()
        ValueError: Can't get non-existent units
        >>> print(d.get_units(None))
        None

        """
        try:
            return self.Units.units
        except AttributeError:
            return super().get_units(default=default)

    @daskified(_DASKIFIED_VERBOSE)
    def get_calendar(self, default=ValueError()):
        """Return the calendar.

        .. seealso:: `del_calendar`, `set_calendar`

        :Parameters:

            default: optional
                Return the value of the *default* parameter if the
                calendar has not been set. If set to an `Exception`
                instance then it will be raised instead.

        :Returns:

                The calendar.

        **Examples**

        >>> d.set_calendar('julian')
        >>> d.get_calendar
        'metres'
        >>> d.del_calendar()
        >>> d.get_calendar()
        ValueError: Can't get non-existent calendar
        >>> print(d.get_calendar(None))
        None

        """
        try:
            return self.Units.calendar
        except (AttributeError, KeyError):
            return super().get_calendar(default=default)

    @daskified(_DASKIFIED_VERBOSE)
    def set_calendar(self, calendar):
        """Set the calendar.

        .. seealso:: `override_calendar`, `override_units`,
                     `del_calendar`, `get_calendar`

        :Parameters:

            value: `str`
                The new calendar.

        :Returns:

            `None`

        **Examples**

        >>> d.set_calendar('none')
        >>> d.get_calendar
        'none'
        >>> d.del_calendar()
        >>> d.get_calendar()
        ValueError: Can't get non-existent calendar
        >>> print(d.get_calendar(None))
        None

        """
        self.Units = Units(self.get_units(default=None), calendar)

    @daskified(_DASKIFIED_VERBOSE)
    def set_units(self, value):
        """Set the units.

        .. seealso:: `override_units`, `del_units`, `get_units`,
                     `has_units`, `Units`

        :Parameters:

            value: `str`
                The new units.

        :Returns:

            `None`

        **Examples**

        >>> d.set_units('watt')
        >>> d.get_units()
        'watt'
        >>> d.del_units()
        >>> d.get_units()
        ValueError: Can't get non-existent units
        >>> print(d.get_units(None))
        None

        """
        self.Units = Units(value, self.get_calendar(default=None))

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    @_deprecated_kwarg_check("i")
    def max(
        self,
        axes=None,
        squeeze=False,
        mtol=1,
        split_every=None,
        inplace=False,
        i=False,
    ):
        """Calculate maximum values.

        Calculates the maximum value or the maximum values along axes.

        See
        https://ncas-cms.github.io/cf-python/analysis.html#collapse-methods
        for mathematical definitions.

         ..seealso:: `sample_size`, `maximum_absolute_value`, `min`

        :Parameters:

            {{collapse axes: (sequence of) `int`, optional}}

            {{collapse squeeze: `bool`, optional}}

            {{mtol: number, optional}}

            {{split_every: `int` or `dict`, optional}}

                .. versionadded:: TODODASKVER

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`
                The collapsed data, or `None` if the operation was
                in-place.

        **Examples**

        >>> a = np.ma.arange(12).reshape(4, 3)
        >>> d = cf.Data(a, 'K')
        >>> d[1, 1] = cf.masked
        >>> print(d.array)
        [[0 1 2]
         [3 -- 5]
         [6 7 8]
         [9 10 11]]
        >>> d.max()
        <CF Data(1, 1): [[11]] K>

        """
        d = _inplace_enabled_define_and_cleanup(self)
        d, _ = _collapse(
            Collapse.max,
            d,
            axis=axes,
            keepdims=not squeeze,
            split_every=split_every,
            mtol=mtol,
        )

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def maximum_absolute_value(
        self, axes=None, squeeze=False, mtol=1, split_every=None, inplace=False
    ):
        """Calculate maximum absolute values.

        Calculates the maximum absolute value or the maximum absolute
        values along axes.

        See
        https://ncas-cms.github.io/cf-python/analysis.html#collapse-methods
        for mathematical definitions.

         ..seealso:: `sample_size`, `max`, `minimum_absolute_value`

        :Parameters:

            {{collapse axes: (sequence of) `int`, optional}}

            {{collapse squeeze: `bool`, optional}}

            {{mtol: number, optional}}

            {{split_every: `int` or `dict`, optional}}

                .. versionadded:: TODODASKVER

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`
                The collapsed data, or `None` if the operation was
                in-place.

        **Examples**

        >>> a = np.ma.arange(12).reshape(4, 3)
        >>> d = cf.Data(a, 'K')
        >>> d[1, 1] = cf.masked
        >>> print(d.array)
        [[-99 1 2]
         [3 -- 5]
         [6 7 8]
         [9 10 11]]
        >>> d.maximum_absolute_value()
        <CF Data(1, 1): [[99]] K>

        """
        d = _inplace_enabled_define_and_cleanup(self)
        d, _ = _collapse(
            Collapse.max_abs,
            d,
            axis=axes,
            keepdims=not squeeze,
            split_every=split_every,
            mtol=mtol,
        )
        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    @_deprecated_kwarg_check("i")
    def min(
        self,
        axes=None,
        squeeze=False,
        mtol=1,
        split_every=None,
        inplace=False,
        i=False,
        _preserve_partitions=False,
    ):
        """Calculate minimum values.

        Calculates the minimum value or the minimum values along axes.

        See
        https://ncas-cms.github.io/cf-python/analysis.html#collapse-methods
        for mathematical definitions.

         ..seealso:: `sample_size`, `max`, `minimum_absolute_value`

        :Parameters:

            {{collapse axes: (sequence of) `int`, optional}}

            {{collapse squeeze: `bool`, optional}}

            {{mtol: number, optional}}

            {{split_every: `int` or `dict`, optional}}

                .. versionadded:: TODODASKVER

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`
                The collapsed data, or `None` if the operation was
                in-place.

        **Examples**

        >>> a = np.ma.arange(12).reshape(4, 3)
        >>> d = cf.Data(a, 'K')
        >>> d[1, 1] = cf.masked
        >>> print(d.array)
        [[0 1 2]
         [3 -- 5]
         [6 7 8]
         [9 10 11]]
        >>> d.min()
        <CF Data(1, 1): [[0]] K>

        """
        d = _inplace_enabled_define_and_cleanup(self)
        d, _ = _collapse(
            Collapse.min,
            d,
            axis=axes,
            keepdims=not squeeze,
            split_every=split_every,
            mtol=mtol,
        )
        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def minimum_absolute_value(
        self, axes=None, squeeze=False, mtol=1, split_every=None, inplace=False
    ):
        """Calculate minimum absolute values.

        Calculates the minimum absolute value or the minimum absolute
        values along axes.

        See
        https://ncas-cms.github.io/cf-python/analysis.html#collapse-methods
        for mathematical definitions.

         ..seealso:: `sample_size`, `maximum_absolute_value`, `min`

        :Parameters:

            {{collapse axes: (sequence of) `int`, optional}}

            {{collapse squeeze: `bool`, optional}}

            {{mtol: number, optional}}

            {{split_every: `int` or `dict`, optional}}

                .. versionadded:: TODODASKVER

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`
                The collapsed data, or `None` if the operation was
                in-place.

        **Examples**

        >>> a = np.ma.arange(12).reshape(4, 3)
        >>> d = cf.Data(a, 'K')
        >>> d[0, 0] = -99
        >>> d[1, 1] = cf.masked
        >>> print(d.array)
        [[-99 1 2]
         [3 -- 5]
         [6 7 8]
         [9 10 11]]
        >>> d.minimum_absolute_value()
        <CF Data(1, 1): [[1]] K>

        """
        d = _inplace_enabled_define_and_cleanup(self)
        d, _ = _collapse(
            Collapse.min_abs,
            d,
            axis=axes,
            keepdims=not squeeze,
            split_every=split_every,
            mtol=mtol,
        )
        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    @_deprecated_kwarg_check("i")
    def mean(
        self,
        axes=None,
        weights=None,
        squeeze=False,
        mtol=1,
        split_every=None,
        inplace=False,
        i=False,
    ):
        """Calculate mean values.

        Calculates the mean value or the mean values along axes.

        See
        https://ncas-cms.github.io/cf-python/analysis.html#collapse-methods
        for mathematical definitions.

         ..seealso:: `sample_size`, `mean_abslute_value`, `sd`, `sum`

        :Parameters:

            {{collapse axes: (sequence of) `int`, optional}}

            {{weights: data_like, `dict`, or `None`, optional}}

            {{collapse squeeze: `bool`, optional}}

            {{mtol: number, optional}}

            {{split_every: `int` or `dict`, optional}}

                .. versionadded:: TODODASKVER

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`
                The collapsed data, or `None` if the operation was
                in-place.

        **Examples**

        >>> a = np.ma.arange(12).reshape(4, 3)
        >>> d = cf.Data(a, 'K')
        >>> d[1, 1] = cf.masked
        >>> print(d.array)
        [[0 1 2]
         [3 -- 5]
         [6 7 8]
         [9 10 11]]
        >>> d.mean()
        <CF Data(1, 1): [[5.636363636363637]] K>

        >>> w = np.linspace(1, 2, 3)
        >>> print(w)
        [1.  1.5 2. ]
        >>> d.mean(weights=w)
        <CF Data(1, 1): [[5.878787878787879]] K>

        """
        d = _inplace_enabled_define_and_cleanup(self)
        d, _ = _collapse(
            Collapse.mean,
            d,
            axis=axes,
            weights=weights,
            keepdims=not squeeze,
            split_every=split_every,
            mtol=mtol,
        )
        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def mean_absolute_value(
        self,
        axes=None,
        squeeze=False,
        mtol=1,
        weights=None,
        split_every=None,
        inplace=False,
    ):
        """Calculate mean absolute values.

        Calculates the mean absolute value or the mean absolute values
        along axes.

        See
        https://ncas-cms.github.io/cf-python/analysis.html#collapse-methods
        for mathematical definitions.

         ..seealso:: `sample_size`, `mean`, `sd`, `sum`

        :Parameters:

            {{collapse axes: (sequence of) `int`, optional}}

            {{weights: data_like, `dict`, or `None`, optional}}

            {{collapse squeeze: `bool`, optional}}

            {{mtol: number, optional}}

            {{split_every: `int` or `dict`, optional}}

                .. versionadded:: TODODASKVER

            {{inplace: `bool`, optional}}

        :Returns:

            `Data` or `None`
                The collapsed data, or `None` if the operation was
                in-place.

        **Examples**

        >>> a = np.ma.arange(12).reshape(4, 3)
        >>> d = cf.Data(a, 'K')
        >>> d[0, 0] = -99
        >>> d[1, 1] = cf.masked
        >>> print(d.array)
        [[-99 1 2]
         [3 -- 5]
         [6 7 8]
         [9 10 11]]
        >>> d.mean_absolute_value()
        <CF Data(1, 1): [[14.636363636363637]] K>

        >>> w = np.linspace(1, 2, 3)
        >>> print(w)
        [1.  1.5 2. ]
        >>> d.mean_absolute_value(weights=w)
        <CF Data(1, 1): [[11.878787878787879]] K>

        """
        d = _inplace_enabled_define_and_cleanup(self)
        d, _ = _collapse(
            Collapse.mean_abs,
            d,
            axis=axes,
            weights=weights,
            keepdims=not squeeze,
            split_every=split_every,
            mtol=mtol,
        )
        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def integral(
        self,
        axes=None,
        squeeze=False,
        mtol=1,
        weights=None,
        split_every=None,
        inplace=False,
        _preserve_partitions=False,
    ):
        """Calculate summed values.

        Calculates the sum value or the sum values along axes.

        See
        https://ncas-cms.github.io/cf-python/analysis.html#collapse-methods
        for mathematical definitions.

         ..seealso:: `sample_size`, `mean`, `sd`, `sum`

        :Parameters:

            {{collapse axes: (sequence of) `int`, optional}}

            {{weights: data_like, `dict`, or `None`, optional}}

            {{collapse squeeze: `bool`, optional}}

            {{mtol: number, optional}}

            {{split_every: `int` or `dict`, optional}}

                .. versionadded:: TODODASKVER

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`
                The collapsed data, or `None` if the operation was
                in-place.

        **Examples**

        >>> a = np.ma.arange(12).reshape(4, 3)
        >>> d = cf.Data(a, 'K')
        >>> d[1, 1] = cf.masked
        >>> print(d.array)
        [[0 1 2]
         [3 -- 5]
         [6 7 8]
         [9 10 11]]
        >>> d.integral()
        <CF Data(1, 1): [[62]] K>

        >>> w = np.linspace(1, 2, 3)
        >>> print(w)
        [1.  1.5 2. ]
        >>> d.integral(weights=w)
        <CF Data(1, 1): [[97.0]] K>

        >>> d.integral(weights=cf.Data(w, 'm'))
        <CF Data(1, 1): [[97.0]] m.K>

        """
        d = _inplace_enabled_define_and_cleanup(self)
        d, weights = _collapse(
            Collapse.sum,
            d,
            axis=axes,
            weights=weights,
            keepdims=not squeeze,
            split_every=split_every,
            mtol=mtol,
        )

        new_units = None
        if weights is not None:
            weights_units = getattr(weights, "Units", None)
            if weights_units:
                units = self.Units
                if units:
                    new_units = units * weights_units
                else:
                    new_units = weights_units

        if new_units is not None:
            d.override_units(new_units, inplace=True)

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    @_deprecated_kwarg_check("i")
    def sample_size(
        self,
        axes=None,
        squeeze=False,
        mtol=1,
        split_every=None,
        inplace=False,
        i=False,
    ):
        """Calculate sample size values.

        The sample size is the number of non-missing values.

        Calculates the sample size value or the sample size values
        along axes.

        .. seealso:: `sum_of_weights`

        :Parameters:

            {{collapse axes: (sequence of) `int`, optional}}

            {{collapse squeeze: `bool`, optional}}

            {{mtol: number, optional}}

            {{split_every: `int` or `dict`, optional}}

                .. versionadded:: TODODASKVER

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`
                The collapsed data, or `None` if the operation was
                in-place.

        **Examples**

        >>> a = np.ma.arange(12).reshape(4, 3)
        >>> d = cf.Data(a, 'K')
        >>> d[1, 1] = cf.masked
        >>> print(d.array)
        [[0 1 2]
         [3 -- 5]
         [6 7 8]
         [9 10 11]]
        >>> d.sample_size()
        <CF Data(1, 1): [[11]]>

        """
        d = _inplace_enabled_define_and_cleanup(self)
        d, _ = _collapse(
            Collapse.sample_size,
            d,
            axis=axes,
            keepdims=not squeeze,
            split_every=split_every,
            mtol=mtol,
        )
        d.override_units(_units_None, inplace=True)

        return d

    @property
    @daskified(_DASKIFIED_VERBOSE)
    def binary_mask(self):
        """A binary (0 and 1) mask of the data array.

        The binary mask's data array comprises dimensionless 32-bit
        integers and has 0 where the data array has missing data and 1
        otherwise.

        .. seealso:: `mask`

        :Returns:

            `Data`
                The binary mask.

        **Examples**

        >>> d = cf.Data([[0, 1, 2, 3]], 'm')
        >>> m = d.binary_mask
        >>> m
        <CF Data(1, 4): [[0, ..., 0]] 1>
        >>> print(m.array)
        [[0 0 0 0]]
        >>> d[0, 1] = cf.masked
        >>> print(d.binary_mask.array)
        [[0 1 0 0]]

        """
        m = self.mask
        m.dtype = "int32"
        m.override_units(_units_1, inplace=True)
        return m

    @daskified(_DASKIFIED_VERBOSE)
    @_deprecated_kwarg_check("i")
    @_inplace_enabled(default=False)
    def clip(self, a_min, a_max, units=None, inplace=False, i=False):
        """Clip (limit) the values in the data array in place.

        Given an interval, values outside the interval are clipped to
        the interval edges. For example, if an interval of [0, 1] is
        specified then values smaller than 0 become 0 and values
        larger than 1 become 1.

        :Parameters:

            a_min: number
                Minimum value. If `None`, clipping is not performed on
                lower interval edge. Not more than one of `a_min` and
                `a_max` may be `None`.

            a_max: number
                Maximum value. If `None`, clipping is not performed on
                upper interval edge. Not more than one of `a_min` and
                `a_max` may be `None`.

            units: `str` or `Units`
                Specify the units of *a_min* and *a_max*. By default the
                same units as the data are assumed.

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`
                The clipped data. If the operation was in-place then
                `None` is returned.


        **Examples**

        >>> d = cf.Data(np.arange(12).reshape(3, 4), 'm')
        >>> print(d.array)
        [[ 0  1  2  3]
         [ 4  5  6  7]
         [ 8  9 10 11]]
        >>> print(d.clip(2, 10).array)
        [[ 2  2  2  3]
         [ 4  5  6  7]
         [ 8  9 10 10]]
        >>> print(d.clip(0.003, 0.009, 'km').array)
        [[3. 3. 3. 3.]
         [4. 5. 6. 7.]
         [8. 9. 9. 9.]]

        """
        if units is not None:
            # Convert the limits to the same units as the data array
            units = Units(units)
            self_units = self.Units
            if self_units != units:
                a_min = Units.conform(np.asanyarray(a_min), units, self_units)
                a_max = Units.conform(np.asanyarray(a_max), units, self_units)

        d = _inplace_enabled_define_and_cleanup(self)
        dx = self.to_dask_array()
        dx = da.clip(dx, a_min, a_max)
        d._set_dask(dx)
        return d

    @classmethod
    @daskified(_DASKIFIED_VERBOSE)
    def asdata(cls, d, dtype=None, copy=False):
        """Convert the input to a `Data` object.

        If the input *d* has the Data interface (i.e. it has a
        `__data__` method), then the output of this method is used as
        the returned `Data` object. Otherwise, `Data(d)` is returned.

        :Parameters:

            d: data-like
                Input data in any form that can be converted to a
                `Data` object. This includes `Data` and `Field`
                objects, and objects with the Data interface, numpy
                arrays and any object which may be converted to a
                numpy array.

           dtype: data-type, optional
                By default, the data-type is inferred from the input data.

           copy: `bool`, optional
                If True and *d* has the Data interface, then a copy of
                `d.__data__()` is returned.

        :Returns:

            `Data`
                `Data` interpretation of *d*. No copy is performed on the
                input if it is already a `Data` object with matching dtype
                and *copy* is False.

        **Examples**

        >>> d = cf.Data([1, 2])
        >>> cf.Data.asdata(d) is d
        True
        >>> d.asdata(d) is d
        True

        >>> cf.Data.asdata([1, 2])
        <CF Data: [1, 2]>

        >>> cf.Data.asdata(numpy.array([1, 2]))
        <CF Data: [1, 2]>

        """
        data = getattr(d, "__data__", None)
        if data is None:
            # d does not have a Data interface
            data = cls(d)
            if dtype is not None:
                data.dtype = dtype

            return data

        # d does have a Data interface
        data = data()
        if copy:
            data = data.copy()
            if dtype is not None and np.dtype(dtype) != data.dtype:
                data.dtype = dtype
        elif dtype is not None and np.dtype(dtype) != data.dtype:
            data = data.copy()
            data.dtype = dtype

        return data

    @_inplace_enabled(default=False)
    def compressed(self, inplace=False):
        """Return all non-masked values in a one dimensional data array.

        Not to be confused with compression by convention (see the
        `uncompress` method).

        .. versionadded:: 3.2.0

        .. seealso:: `flatten`

        :Parameters:

            {{inplace: `bool`, optional}}

        :Returns:

            `Data` or `None`
                The non-masked values, or `None` if the operation was
                in-place.

        **Examples**

        >>> d = cf.Data(numpy.arange(12).reshape(3, 4), 'm')
        >>> print(d.array)
        [[ 0  1  2  3]
         [ 4  5  6  7]
         [ 8  9 10 11]]
        >>> print(d.compressed().array)
        [ 0  1  2  3  4  5  6  7  8  9 10 11]
        >>> d[1, 1] = cf.masked
        >>> d[2, 3] = cf.masked
        >>> print(d.array)
        [[0  1  2  3]
         [4 --  6  7]
         [8  9 10 --]]
        >>> print(d.compressed().array)
        [ 0  1  2  3  4  6  7  8  9 10]

        >>> d = cf.Data(9)
        >>> print(d.compressed().array)
        [9]

        """
        d = _inplace_enabled_define_and_cleanup(self)

        dx = d.to_dask_array()
        dx = da.blockwise(
            np.ma.compressed,
            "i",
            dx.ravel(),
            "i",
            adjust_chunks={"i": lambda n: np.nan},
            dtype=dx.dtype,
            meta=np.array((), dtype=dx.dtype),
        )

        d._set_dask(dx)
        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_deprecated_kwarg_check("i")
    @_inplace_enabled(default=False)
    def cos(self, inplace=False, i=False):
        """Take the trigonometric cosine of the data element-wise.

        Units are accounted for in the calculation. If the units are not
        equivalent to radians (such as Kelvin) then they are treated as if
        they were radians. For example, the cosine of 90 degrees_east
        is 0.0, as is the cosine of 1.57079632 kg m-2.

        The output units are changed to '1' (nondimensional).

        .. seealso:: `arccos`, `sin`, `tan`, `cosh`

        :Parameters:

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`

        **Examples**

        >>> d.Units
        <Units: degrees_east>
        >>> print(d.array)
        [[-90 0 90 --]]
        >>> e = d.cos()
        >>> e.Units
        <Units: 1>
        >>> print(e.array)
        [[0.0 1.0 0.0 --]]

        >>> d.Units
        <Units: m s-1>
        >>> print(d.array)
        [[1 2 3 --]]
        >>> d.cos(inplace=True)
        >>> d.Units
        <Units: 1>
        >>> print(d.array)
        [[0.540302305868 -0.416146836547 -0.9899924966 --]]

        """
        d = _inplace_enabled_define_and_cleanup(self)

        if d.Units.equivalent(_units_radians):
            d.Units = _units_radians

        dx = d.to_dask_array()
        d._set_dask(da.cos(dx))

        d.override_units(_units_1, inplace=True)

        return d

    @daskified(_DASKIFIED_VERBOSE)
    def count(self, axis=None, keepdims=True, split_every=None):
        """Count the non-masked elements of the data.

        .. seealso:: `count_masked`

        :Parameters:

            axis: (sequence of) `int`, optional
                Axis or axes along which the count is performed. The
                default (`None`) performs the count over all the
                dimensions of the input array. *axis* may be negative,
                in which case it counts from the last to the first
                axis.

            {{collapse keepdims: `bool`, optional}}

            {{split_every: `int` or `dict`, optional}}

        :Returns:

            `Data`
                The count of non-missing elements.

        **Examples**

        >>> d = cf.Data(numpy.arange(12).reshape(3, 4))
        >>> print(d.array)
        [[ 0  1  2  3]
         [ 4  5  6  7]
         [ 8  9 10 11]]
        >>> d.count()
        <CF Data(1, 1): [[12]]>

        >>> d[0, :] = cf.masked
        >>> print(d.array)
        [[-- -- -- --]
         [ 4  5  6  7]
         [ 8  9 10 11]]
        >>> d.count()
        <CF Data(1, 1): [[8]]>

        >>> print(d.count(0).array)
        [[2 2 2 2]]
        >>> print(d.count(1).array)
        [[0]
         [4]
         [4]]
        >>> print(d.count([0, 1], keepdims=False).array)
        8

        """
        d = self.copy(array=False)
        dx = self.to_dask_array()
        dx = da.ma.count(
            dx, axis=axis, keepdims=keepdims, split_every=split_every
        )
        d._set_dask(dx)
        d.hardmask = _DEFAULT_HARDMASK
        d.override_units(_units_None, inplace=True)
        return d

    @daskified(_DASKIFIED_VERBOSE)
    def count_masked(self, split_every=None):
        """Count the masked elements of the data.

        .. seealso:: `count`

        :Parameters:

            {{split_every: `int` or `dict`, optional}}

        :Returns:

            `Data`
                The count of missing elements.

        **Examples**

        >>> d = cf.Data(numpy.arange(12).reshape(3, 4))
        >>> print(d.array)
        [[ 0  1  2  3]
         [ 4  5  6  7]
         [ 8  9 10 11]]
        >>> d.count_masked()
        <CF Data(1, 1): [[0]]>

        >>> d[0, :] = cf.masked
        >>> print(d.array)
        [[-- -- -- --]
         [ 4  5  6  7]
         [ 8  9 10 11]]
        >>> d.count_masked()
        <CF Data(1, 1): [[4]]>

        """
        return self.size - self.count(split_every=split_every)

    @daskified(_DASKIFIED_VERBOSE)
    def cyclic(self, axes=None, iscyclic=True):
        """Get or set the cyclic axes.

        Some methods treat the first and last elements of a cyclic
        axis as adjacent and physically connected, such as
        `convolution_filter`, `__getitem__` and `__setitem__`. Some
        methods may make a cyclic axis non-cyclic, such as `halo`.

        :Parameters:

            axes: (sequence of) `int`, optional
                Select the axes to have their cyclicity set. By
                default, or if *axes* is `None` or an empty sequence,
                no axes are modified.

            iscyclic: `bool`
                Specify whether to make the axes cyclic or
                non-cyclic. By default (True), the axes are set as
                cyclic.

        :Returns:

            `set`
                The cyclic axes prior to the change, or the current
                cylcic axes if no axes are specified.

        **Examples**

        >>> d = cf.Data(np.arange(12).reshape(3, 4))
        >>> d.cyclic()
        set()
        >>> d.cyclic(0)
        set()
        >>> d.cyclic()
        {0}
        >>> d.cyclic(0, iscyclic=False)
        {0}
        >>> d.cyclic()
        set()
        >>> d.cyclic([0, 1])
        set()
        >>> d.cyclic()
        {0, 1}
        >>> d.cyclic([0, 1], iscyclic=False)
        {0, 1}
        >>> d.cyclic()
        set()

        >>> print(d.array)
        [[ 0  1  2  3]
         [ 4  5  6  7]
         [ 8  9 10 11]]
        >>> d[0, -1:2]
        Traceback (most recent call last):
            ...
        IndexError: Can't take a cyclic slice of a non-cyclic axis
        >>> d.cyclic(1)
        set()
        >>> d[0, -1:2]
        <CF Data(1, 2): [[3, 0, 1]]>

        """
        cyclic_axes = self._cyclic
        data_axes = self._axes

        old = set([data_axes.index(axis) for axis in cyclic_axes])

        if axes is None:
            return old

        axes = [data_axes[i] for i in self._parse_axes(axes)]

        # Never change the value of the _cyclic attribute in-place
        if iscyclic:
            self._cyclic = cyclic_axes.union(axes)
        else:
            self._cyclic = cyclic_axes.difference(axes)

        return old

    @property
    @daskified(_DASKIFIED_VERBOSE)
    def year(self):
        """The year of each date-time value.

        Only applicable for data with reference time units. The
        returned `Data` will have the same mask hardness as the
        original array.

        .. seealso:: `~cf.Data.month`, `~cf.Data.day`, `~cf.Data.hour`,
                     `~cf.Data.minute`, `~cf.Data.second`

        **Examples**

        >>> d = cf.Data([[1.93, 5.17]], 'days since 2000-12-29')
        >>> d
        <CF Data(1, 2): [[2000-12-30 22:19:12, 2001-01-03 04:04:48]] >
        >>> d.year
        <CF Data(1, 2): [[2000, 2001]] >

        """
        return YMDhms(self, "year")

    @property
    @daskified(_DASKIFIED_VERBOSE)
    def month(self):
        """The month of each date-time value.

        Only applicable for data with reference time units. The
        returned `Data` will have the same mask hardness as the
        original array.

        .. seealso:: `~cf.Data.year`, `~cf.Data.day`, `~cf.Data.hour`,
                     `~cf.Data.minute`, `~cf.Data.second`

        **Examples**

        >>> d = cf.Data([[1.93, 5.17]], 'days since 2000-12-29')
        >>> d
        <CF Data(1, 2): [[2000-12-30 22:19:12, 2001-01-03 04:04:48]] >
        >>> d.month
        <CF Data(1, 2): [[12, 1]] >

        """
        return YMDhms(self, "month")

    @property
    @daskified(_DASKIFIED_VERBOSE)
    def day(self):
        """The day of each date-time value.

        Only applicable for data with reference time units. The
        returned `Data` will have the same mask hardness as the
        original array.

        .. seealso:: `~cf.Data.year`, `~cf.Data.month`, `~cf.Data.hour`,
                     `~cf.Data.minute`, `~cf.Data.second`

        **Examples**

        >>> d = cf.Data([[1.93, 5.17]], 'days since 2000-12-29')
        >>> d
        <CF Data(1, 2): [[2000-12-30 22:19:12, 2001-01-03 04:04:48]] >
        >>> d.day
        <CF Data(1, 2): [[30, 3]] >

        """
        return YMDhms(self, "day")

    @property
    @daskified(_DASKIFIED_VERBOSE)
    def hour(self):
        """The hour of each date-time value.

        Only applicable for data with reference time units. The
        returned `Data` will have the same mask hardness as the
        original array.

        .. seealso:: `~cf.Data.year`, `~cf.Data.month`, `~cf.Data.day`,
                     `~cf.Data.minute`, `~cf.Data.second`

        **Examples**

        >>> d = cf.Data([[1.93, 5.17]], 'days since 2000-12-29')
        >>> d
        <CF Data(1, 2): [[2000-12-30 22:19:12, 2001-01-03 04:04:48]] >
        >>> d.hour
        <CF Data(1, 2): [[22, 4]] >

        """
        return YMDhms(self, "hour")

    @property
    @daskified(_DASKIFIED_VERBOSE)
    def minute(self):
        """The minute of each date-time value.

        Only applicable for data with reference time units. The
        returned `Data` will have the same mask hardness as the
        original array.

        .. seealso:: `~cf.Data.year`, `~cf.Data.month`, `~cf.Data.day`,
                     `~cf.Data.hour`, `~cf.Data.second`

        **Examples**

        >>> d = cf.Data([[1.93, 5.17]], 'days since 2000-12-29')
        >>> d
        <CF Data(1, 2): [[2000-12-30 22:19:12, 2001-01-03 04:04:48]] >
        >>> d.minute
        <CF Data(1, 2): [[19, 4]] >

        """
        return YMDhms(self, "minute")

    @property
    @daskified(_DASKIFIED_VERBOSE)
    def second(self):
        """The second of each date-time value.

        Only applicable for data with reference time units. The
        returned `Data` will have the same mask hardness as the
        original array.

        .. seealso:: `~cf.Data.year`, `~cf.Data.month`, `~cf.Data.day`,
                     `~cf.Data.hour`, `~cf.Data.minute`

        **Examples**

        >>> d = cf.Data([[1.93, 5.17]], 'days since 2000-12-29')
        >>> d
        <CF Data(1, 2): [[2000-12-30 22:19:12, 2001-01-03 04:04:48]] >
        >>> d.second
        <CF Data(1, 2): [[12, 48]] >

        """
        return YMDhms(self, "second")

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def uncompress(self, inplace=False):
        """Uncompress the data.

        Only affects data that is compressed by convention, i.e.

          * Ragged arrays for discrete sampling geometries (DSG) and
            simple geometry cell definitions.

          * Compression by gathering.

          * Compression by coordinate subsampling.

        Data that is already uncompressed is returned
        unchanged. Whether the data is compressed or not does not
        alter its functionality nor external appearance, but may
        affect how the data are written to a dataset on disk.

        .. versionadded:: 3.0.6

        .. seealso:: `array`, `compressed_array`, `source`

        :Parameters:

            {{inplace: `bool`, optional}}

        :Returns:

            `Data` or `None`
                The uncompressed data, or `None` if the operation was
                in-place.

        **Examples**

        >>> d.get_compression_type()
        'ragged contiguous'
        >>> d.uncompress()
        >>> d.get_compression_type()
        ''

        """
        d = _inplace_enabled_define_and_cleanup(self)
        if d.get_compression_type():
            d._del_Array(None)

        return d

    @daskified(_DASKIFIED_VERBOSE)
    def unique(self, split_every=None):
        """The unique elements of the data.

        Returns the sorted unique elements of the array.

        :Parameters:

            {{split_every: `int` or `dict`, optional}}

        :Returns:

            `Data`
                The unique values in a 1-d array.

        **Examples**

        >>> d = cf.Data([[4, 2, 1], [1, 2, 3]], 'metre')
        >>> print(d.array)
        [[4 2 1]
         [1 2 3]]
        >>> e = d.unique()
        >>> e
        <CF Data(4): [1, ..., 4] metre>
        >>> print(e.array)
        [1 2 3 4]
        >>> d[0, 0] = cf.masked
        >>> print(d.array)
        [[-- 2 1]
         [1 2 3]]
        >>> e = d.unique()
        >>> print(e.array)
        [1 2 3 --]

        """
        d = self.copy()

        # Soften the hardmask so that the result doesn't contain a
        # seperate missing value for each input chunk that contains
        # missing values. For any number greater than 0 of missing
        # values in the original data, we only want one missing value
        # in the result.
        d.soften_mask()

        dx = d.to_dask_array()
        dx = Collapse.unique(dx, split_every=split_every)

        d._set_dask(dx)

        d.hardmask = _DEFAULT_HARDMASK

        return d

    @_display_or_return
    def dump(self, display=True, prefix=None):
        """Return a string containing a full description of the
        instance.

        :Parameters:

            display: `bool`, optional
                If False then return the description as a string. By
                default the description is printed, i.e. ``d.dump()`` is
                equivalent to ``print(d.dump(display=False))``.

            prefix: `str`, optional
               Set the common prefix of component names. By default the
               instance's class name is used.

        :Returns:

            `None` or `str`
                A string containing the description.

        """
        if prefix is None:
            prefix = self.__class__.__name__

        string = [f"{prefix}.shape = {self.shape}"]

        if self.size == 1:
            string.append(f"{prefix}.first_datum = {self.datum(0)}")
        else:
            string.append(f"{prefix}.first_datum = {self.datum(0)}")
            string.append(f"{prefix}.last_datum  = {self.datum(-1)}")

        for attr in ("fill_value", "Units"):
            string.append(f"{prefix}.{attr} = {getattr(self, attr)!r}")

        return "\n".join(string)

    def ndindex(self):
        """Return an iterator over the N-dimensional indices of the data
        array.

        At each iteration a tuple of indices is returned, the last
        dimension is iterated over first.

        :Returns:

            `itertools.product`
                An iterator over tuples of indices of the data array.

        **Examples**

        >>> d = cf.Data(np.arange(6).reshape(2, 3))
        >>> print(d.array)
        [[0 1 2]
         [3 4 5]]
        >>> for i in d.ndindex():
        ...     print(i, d[i])
        ...
        (0, 0) [[0]]
        (0, 1) [[1]]
        (0, 2) [[2]]
        (1, 0) [[3]]
        (1, 1) [[4]]
        (1, 2) [[5]]

        >>> d = cf.Data(9)
        >>> for i in d.ndindex():
        ...     print(i, d[i])
        ...
        () 9

        """
        return product(*[range(0, r) for r in self.shape])

    @daskified(_DASKIFIED_VERBOSE)
    @_deprecated_kwarg_check("traceback")
    @_manage_log_level_via_verbosity
    def equals(
        self,
        other,
        rtol=None,
        atol=None,
        ignore_fill_value=False,
        ignore_data_type=False,
        ignore_type=False,
        verbose=None,
        traceback=False,
        ignore_compression=False,
    ):
        """True if two data arrays are logically equal, False otherwise.

        {{equals tolerance}}

        :Parameters:

            other:
                The object to compare for equality.

            {{rtol: number, optional}}

            {{atol: number, optional}}

            ignore_fill_value: `bool`, optional
                If True then data arrays with different fill values are
                considered equal. By default they are considered unequal.

            {{ignore_data_type: `bool`, optional}}

            {{ignore_type: `bool`, optional}}

            {{verbose: `int` or `str` or `None`, optional}}

            traceback: deprecated at version 3.0.0
                Use the *verbose* parameter instead.

            {{ignore_compression: `bool`, optional}}

        :Returns:

            `bool`
                Whether or not the two instances are equal.

        **Examples**

        >>> d.equals(d)
        True
        >>> d.equals(d + 1)
        False

        """
        # Set default tolerances
        if rtol is None:
            rtol = self._rtol

        if atol is None:
            atol = self._atol

        if not super().equals(
            other,
            rtol=rtol,
            atol=atol,
            verbose=verbose,
            ignore_data_type=ignore_data_type,
            ignore_fill_value=ignore_fill_value,
            ignore_type=ignore_type,
            _check_values=False,
        ):
            # TODODASK: consistency with cfdm Data.equals needs to be verified
            # possibly via a follow-up PR to cfdm to implement any changes.
            return False

        # ------------------------------------------------------------
        # Check that each instance has equal array values
        # ------------------------------------------------------------
        # Check that each instance has the same units
        self_Units = self.Units
        other_Units = other.Units
        if self_Units != other_Units:
            logger.info(
                f"{self.__class__.__name__}: Different Units "
                f"({self.Units!r}, {other.Units!r})"
            )
            return False

        self_dx = self.to_dask_array()
        other_dx = other.to_dask_array()

        # Now check that corresponding elements are equal within a tolerance.
        # We assume that all inputs are masked arrays. Note we compare the
        # data first as this may return False due to different dtype without
        # having to wait until the compute call.
        self_is_numeric = _is_numeric_dtype(self_dx)
        other_is_numeric = _is_numeric_dtype(other_dx)
        if self_is_numeric and other_is_numeric:
            data_comparison = _da_ma_allclose(
                self_dx,
                other_dx,
                masked_equal=True,
                rtol=float(rtol),
                atol=float(atol),
            )
        elif not self_is_numeric and not other_is_numeric:
            data_comparison = da.all(self_dx == other_dx)
        else:  # one is numeric and other isn't => not equal (incompat. dtype)
            logger.info(
                f"{self.__class__.__name__}: Different data types:"
                f"{self_dx.dtype} != {other_dx.dtype}"
            )
            return False

        mask_comparison = da.all(
            da.equal(da.ma.getmaskarray(self_dx), da.ma.getmaskarray(other_dx))
        )

        # Apply a (dask) logical 'and' to confirm if both the mask and the
        # data are equal for the pair of masked arrays:
        result = da.logical_and(data_comparison, mask_comparison)

        if not result.compute():
            logger.info(
                f"{self.__class__.__name__}: Different array values ("
                f"atol={atol}, rtol={rtol})"
            )
            return False
        else:
            return True

    @daskified(_DASKIFIED_VERBOSE)
    @_deprecated_kwarg_check("i")
    @_inplace_enabled(default=False)
    def exp(self, inplace=False, i=False):
        """Take the exponential of the data array.

        :Parameters:

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`

        **Examples**

        """
        d = _inplace_enabled_define_and_cleanup(self)

        units = self.Units
        if units and not units.isdimensionless:
            raise ValueError(
                "Can't take exponential of dimensional "
                f"quantities: {units!r}"
            )

        if d.Units:
            d.Units = _units_1

        dx = d.to_dask_array()
        d._set_dask(da.exp(dx))

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def insert_dimension(self, position=0, inplace=False):
        """Expand the shape of the data array in place.

        .. seealso:: `flip`, `squeeze`, `swapaxes`, `transpose`

        :Parameters:

            position: `int`, optional
                Specify the position that the new axis will have in the data
                array axes. By default the new axis has position 0, the
                slowest varying position.

            {{inplace: `bool`, optional}}

        :Returns:

            `Data` or `None`

        **Examples**

        """
        # TODODASKAPI bring back expand_dime alias (or rather alias this to
        # that)

        d = _inplace_enabled_define_and_cleanup(self)

        # Parse position
        if not isinstance(position, int):
            raise ValueError("Position parameter must be an integer")

        ndim = d.ndim
        if -ndim - 1 <= position < 0:
            position += ndim + 1
        elif not 0 <= position <= ndim:
            raise ValueError(
                f"Can't insert dimension: Invalid position {position!r}"
            )

        shape = list(d.shape)
        shape.insert(position, 1)

        dx = d.to_dask_array()
        dx = dx.reshape(shape)
        d._set_dask(dx)

        # Expand _axes
        axis = new_axis_identifier(d._axes)
        data_axes = list(d._axes)
        data_axes.insert(position, axis)
        d._axes = data_axes

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_deprecated_kwarg_check("size")
    @_inplace_enabled(default=False)
    @_manage_log_level_via_verbosity
    def halo(
        self,
        depth,
        axes=None,
        tripolar=None,
        fold_index=-1,
        inplace=False,
        verbose=None,
        size=None,
    ):
        """Expand the data by adding a halo.

        The halo contains the adjacent values up to the given
        depth(s). See the example for details.

        The halo may be applied over a subset of the data dimensions
        and each dimension may have a different halo size (including
        zero). The halo region is populated with a copy of the
        proximate values from the original data.

        **Cyclic axes**

        A cyclic axis that is expanded with a halo of at least size 1
        is no longer considered to be cyclic.

        **Tripolar domains**

        Data for global tripolar domains are a special case in that a
        halo added to the northern end of the "Y" axis must be filled
        with values that are flipped in "X" direction. Such domains
        need to be explicitly indicated with the *tripolar* parameter.

        .. versionadded:: 3.5.0

        :Parameters:

            depth: `int` or `dict`
                Specify the size of the halo for each axis.

                If *depth* is a non-negative `int` then this is the
                halo size that is applied to all of the axes defined
                by the *axes* parameter.

                Alternatively, halo sizes may be assigned to axes
                individually by providing a `dict` for which a key
                specifies an axis (defined by its integer position in
                the data) with a corresponding value of the halo size
                for that axis. Axes not specified by the dictionary
                are not expanded, and the *axes* parameter must not
                also be set.

                *Parameter example:*
                  Specify a halo size of 1 for all otherwise selected
                  axes: ``depth=1``.

                *Parameter example:*
                  Specify a halo size of zero ``depth=0``. This
                  results in no change to the data shape.

                *Parameter example:*
                  For data with three dimensions, specify a halo size
                  of 3 for the first dimension and 1 for the second
                  dimension: ``depth={0: 3, 1: 1}``. This is
                  equivalent to ``depth={0: 3, 1: 1, 2: 0}``.

                *Parameter example:*
                  Specify a halo size of 2 for the first and last
                  dimensions `depth=2, axes=[0, -1]`` or equivalently
                  ``depth={0: 2, -1: 2}``.

            axes: (sequence of) `int`
                Select the domain axes to be expanded, defined by
                their integer positions in the data. By default, or if
                *axes* is `None`, all axes are selected. No axes are
                expanded if *axes* is an empty sequence.

            tripolar: `dict`, optional
                A dictionary defining the "X" and "Y" axes of a global
                tripolar domain. This is necessary because in the
                global tripolar case the "X" and "Y" axes need special
                treatment, as described above. It must have keys
                ``'X'`` and ``'Y'``, whose values identify the
                corresponding domain axis construct by their integer
                positions in the data.

                The "X" and "Y" axes must be a subset of those
                identified by the *depth* or *axes* parameter.

                See the *fold_index* parameter.

                *Parameter example:*
                  Define the "X" and Y" axes by positions 2 and 1
                  respectively of the data: ``tripolar={'X': 2, 'Y':
                  1}``

            fold_index: `int`, optional
                Identify which index of the "Y" axis corresponds to
                the fold in "X" axis of a tripolar grid. The only
                valid values are ``-1`` for the last index, and ``0``
                for the first index. By default it is assumed to be
                the last index. Ignored if *tripolar* is `None`.

            {{inplace: `bool`, optional}}

            {{verbose: `int` or `str` or `None`, optional}}

            size: deprecated at version TODODASKVER
                Use the *depth* parameter instead.

        :Returns:

            `Data` or `None`
                The expanded data, or `None` if the operation was
                in-place.

        **Examples**

        >>> d = cf.Data(numpy.arange(12).reshape(3, 4), 'm')
        >>> d[-1, -1] = cf.masked
        >>> d[1, 1] = cf.masked
        >>> print(d.array)
        [[0 1 2 3]
         [4 -- 6 7]
         [8 9 10 --]]

        >>> e = d.halo(1)
        >>> print(e.array)
        [[0 0 1 2 3 3]
         [0 0 1 2 3 3]
         [4 4 -- 6 7 7]
         [8 8 9 10 -- --]
         [8 8 9 10 -- --]]

        >>> d.equals(e[1:-1, 1:-1])
        True

        >>> e = d.halo(2)
        >>> print(e.array)
        [[0 1 0 1 2 3 2 3]
         [4 -- 4 -- 6 7 6 7]
         [0 1 0 1 2 3 2 3]
         [4 -- 4 -- 6 7 6 7]
         [8 9 8 9 10 -- 10 --]
         [4 -- 4 -- 6 7 6 7]
         [8 9 8 9 10 -- 10 --]]
        >>> d.equals(e[2:-2, 2:-2])
        True

        >>> e = d.halo(0)
        >>> d.equals(e)
        True

        >>> e = d.halo(1, axes=0)
        >>> print(e.array)
        [[0 1 2 3]
         [0 1 2 3]
         [4 -- 6 7]
         [8 9 10 --]
         [8 9 10 --]]

        >>> d.equals(e[1:-1, :])
        True
        >>> f = d.halo({0: 1})
        >>> f.equals(e)
        True

        >>> e = d.halo(1, tripolar={'X': 1, 'Y': 0})
        >>> print(e.array)
        [[0 0 1 2 3 3]
         [0 0 1 2 3 3]
         [4 4 -- 6 7 7]
         [8 8 9 10 -- --]
         [-- -- 10 9 8 8]]

        >>> e = d.halo(1, tripolar={'X': 1, 'Y': 0}, fold_index=0)
        >>> print(e.array)
        [[3 3 2 1 0 0]
         [0 0 1 2 3 3]
         [4 4 -- 6 7 7]
         [8 8 9 10 -- --]
         [8 8 9 10 -- --]]

        """
        from dask.array.core import concatenate

        d = _inplace_enabled_define_and_cleanup(self)

        ndim = d.ndim
        shape = d.shape

        # Parse the depth and axes parameters
        if isinstance(depth, dict):
            if axes is not None:
                raise ValueError(
                    "Can't set the axes parameter when the "
                    "depth parameter is a dictionary"
                )

            # Check that the dictionary keys are OK and remove size
            # zero depths
            axes = self._parse_axes(tuple(depth))
            depth = {i: size for i, size in depth.items() if size}
        else:
            if axes is None:
                axes = list(range(ndim))
            else:
                axes = d._parse_axes(axes)

            depth = {i: depth for i in axes}

        # Return if all axis depths are zero
        if not any(depth.values()):
            return d

        # Parse the tripolar parameter
        if tripolar:
            if fold_index not in (0, -1):
                raise ValueError(
                    "fold_index parameter must be -1 or 0. "
                    f"Got {fold_index!r}"
                )

            # Find the X and Y axes of a tripolar grid
            tripolar = tripolar.copy()
            X_axis = tripolar.pop("X", None)
            Y_axis = tripolar.pop("Y", None)

            if tripolar:
                raise ValueError(
                    f"Can not set key {tripolar.popitem()[0]!r} in the "
                    "tripolar dictionary."
                )

            if X_axis is None:
                raise ValueError("Must provide a tripolar 'X' axis.")

            if Y_axis is None:
                raise ValueError("Must provide a tripolar 'Y' axis.")

            X = d._parse_axes(X_axis)
            Y = d._parse_axes(Y_axis)

            if len(X) != 1:
                raise ValueError(
                    "Must provide exactly one tripolar 'X' axis. "
                    f"Got {X_axis!r}"
                )

            if len(Y) != 1:
                raise ValueError(
                    "Must provide exactly one tripolar 'Y' axis. "
                    f"Got {Y_axis!r}"
                )

            X_axis = X[0]
            Y_axis = Y[0]

            if X_axis == Y_axis:
                raise ValueError(
                    "Tripolar 'X' and 'Y' axes must be different. "
                    f"Got {X_axis!r}, {Y_axis!r}"
                )

            for A, axis in zip(("X", "Y"), (X_axis, Y_axis)):
                if axis not in axes:
                    raise ValueError(
                        "If dimensions have been identified with the "
                        "axes or depth parameters then they must include "
                        f"the tripolar {A!r} axis: {axis!r}"
                    )

            tripolar = Y_axis in depth

        # Create the halo
        dx = d.to_dask_array()

        indices = [slice(None)] * ndim
        for axis, size in sorted(depth.items()):
            if not size:
                continue

            if size > shape[axis]:
                raise ValueError(
                    f"Halo depth {size} is too large for axis of size "
                    f"{shape[axis]}"
                )

            left_indices = indices[:]
            right_indices = indices[:]

            left_indices[axis] = slice(0, size)
            right_indices[axis] = slice(-size, None)

            left = dx[tuple(left_indices)]
            right = dx[tuple(right_indices)]

            dx = concatenate([left, dx, right], axis=axis)

        d._set_dask(dx)

        # Special case for tripolar: The northern Y axis halo contains
        # the values that have been flipped in the X direction.
        if tripolar:
            # Make sure that we can overwrite any missing values in
            # the northern Y axis halo
            d.soften_mask()

            indices1 = indices[:]
            if fold_index == -1:
                # The last index of the Y axis corresponds to the fold
                # in X axis of a tripolar grid
                indices1[Y_axis] = slice(-depth[Y_axis], None)
            else:
                # The first index of the Y axis corresponds to the
                # fold in X axis of a tripolar grid
                indices1[Y_axis] = slice(0, depth[Y_axis])

            indices2 = indices1[:]
            indices2[X_axis] = slice(None, None, -1)

            dx = d.to_dask_array()
            dx[tuple(indices1)] = dx[tuple(indices2)]

            d._set_dask(dx)

            # Reset the mask hardness
            d.hardmask = self.hardmask

        # Set expanded axes to be non-cyclic
        d.cyclic(axes=tuple(depth), iscyclic=False)

        return d

    def harden_mask(self):
        """Force the mask to hard.

        Whether the mask of a masked array is hard or soft is
        determined by its `hardmask` property. `harden_mask` sets
        `hardmask` to `True`.

        .. versionadded:: TODODASKVER

        .. seealso:: `hardmask`, `soften_mask`

        **Examples**

        >>> d = cf.Data([1, 2, 3], hardmask=False)
        >>> d.hardmask
        False
        >>> d.harden_mask()
        >>> d.hardmask
        True

        >>> d = cf.Data([1, 2, 3], mask=[False, True, False])
        >>> d.hardmask
        True
        >>> d[1] = 999
        >>> print(d.array)
        [1 -- 3]

        """
        dx = self.to_dask_array()
        dx = dx.map_blocks(cf_harden_mask, dtype=self.dtype)
        self._set_dask(dx, delete_source=False)
        self.hardmask = True

    def has_calendar(self):
        """Whether a calendar has been set.

        .. seealso:: `del_calendar`, `get_calendar`, `set_calendar`,
                     `has_units`, `Units`

        :Returns:

            `bool`
                True if the calendar has been set, otherwise False.

        **Examples**

        >>> d = cf.Data(1, "days since 2000-1-1", calendar="noleap")
        >>> d.has_calendar()
        True

        >>> d = cf.Data(1, calendar="noleap")
        >>> d.has_calendar()
        True

        >>> d = cf.Data(1, "days since 2000-1-1")
        >>> d.has_calendar()
        False

        >>> d = cf.Data(1, "m")
        >>> d.has_calendar()
        False

        """
        return hasattr(self.Units, "calendar")

    def has_units(self):
        """Whether units have been set.

        .. seealso:: `del_units`, `get_units`, `set_units`,
                     `has_calendar`, `Units`

        :Returns:

            `bool`
                True if units have been set, otherwise False.

        **Examples**

        >>> d = cf.Data(1, "")
        >>> d.has_units()
        True

        >>> d = cf.Data(1, "m")
        >>> d.has_units()
        True

        >>> d = cf.Data(1)
        >>> d.has_units()
        False

        >>> d = cf.Data(1, calendar='noleap')
        >>> d.has_units()
        False

        """
        return hasattr(self.Units, "units")

    def soften_mask(self):
        """Force the mask to soft.

        Whether the mask of a masked array is hard or soft is
        determined by its `hardmask` property. `soften_mask` sets
        `hardmask` to `False`.

        .. versionadded:: TODODASKVER

        .. seealso:: `hardmask`, `harden_mask`

        **Examples**

        >>> d = cf.Data([1, 2, 3])
        >>> d.hardmask
        True
        >>> d.soften_mask()
        >>> d.hardmask
        False

        >>> d = cf.Data([1, 2, 3], mask=[False, True, False], hardmask=False)
        >>> d.hardmask
        False
        >>> d[1] = 999
        >>> print(d.array)
        [  1 999   3]

        """
        dx = self.to_dask_array()
        dx = dx.map_blocks(cf_soften_mask, dtype=self.dtype)
        self._set_dask(dx, delete_source=False)
        self.hardmask = False

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def filled(self, fill_value=None, inplace=False):
        """Replace masked elements with a fill value.

        .. versionadded:: 3.4.0

        :Parameters:

            fill_value: scalar, optional
                The fill value. By default the fill returned by
                `get_fill_value` is used, or if this is not set then the
                netCDF default fill value for the data type is used (as
                defined by `netCDF.fillvals`).

            {{inplace: `bool`, optional}}

        :Returns:

            `Data` or `None`
                The filled data, or `None` if the operation was in-place.

        **Examples**

        >>> d = cf.Data([[1, 2, 3]])
        >>> print(d.filled().array)
        [[1 2 3]]
        >>> d[0, 0] = cf.masked
        >>> print(d.filled().array)
        [-9223372036854775806                    2                    3]
        >>> d.set_fill_value(-99)
        >>> print(d.filled().array)
        [[-99   2   3]]

        """
        d = _inplace_enabled_define_and_cleanup(self)

        if fill_value is None:
            fill_value = d.get_fill_value(None)
            if fill_value is None:  # still...
                fill_value = default_netCDF_fillvals().get(d.dtype.str[1:])
                if fill_value is None and d.dtype.kind in ("SU"):
                    fill_value = default_netCDF_fillvals().get("S1", None)

                if fill_value is None:
                    raise ValueError(
                        "Can't determine fill value for "
                        f"data type {d.dtype.str!r}"
                    )

        dx = d.to_dask_array()
        dx = dx.map_blocks(np.ma.filled, fill_value=fill_value, dtype=d.dtype)
        d._set_dask(dx)

        return d

    def first_element(self, verbose=None):
        """Return the first element of the data as a scalar.

        If the value is deemed too expensive to compute then a
        `ValueError` is raised instead. It is considered acceptable to
        compute the value in the following circumstances:

        * The `force_compute` attribute is True.

        * The current log level is ``'DEBUG'``.

        * The stored computations consist only of initialisation,
          subspace or copy functions.

        .. versionadded:: 4.0.0

        .. seealso:: `last_element`, `second_element`

        :Returns:

                The first element of the data

        **Examples**

        >>> d = cf.Data([[1, 2], [3, 4]])
        >>> d.first_element()
        1
        >>> d[0, 0] = cf.masked
        >>> d.first_element()
        masked

        """
        if self.can_compute():
            return super().first_element()

        raise ValueError(
            "First element of the data is considered too expensive "
            "to compute. Consider setting the 'force_compute' attribute, or "
            "setting the log level to 'DEBUG'."
        )

    def second_element(self, verbose=None):
        """Return the second element of the data as a scalar.

        If the value is deemed too expensive to compute then a
        `ValueError` is raised instead. It is considered acceptable to
        compute the value in the following circumstances:

        * The `force_compute` attribute is True.

        * The current log level is ``'DEBUG'``.

        * The stored computations consist only of initialisation,
          subspace or copy functions.

        .. versionadded:: 4.0.0

        .. seealso:: `last_element`, `first_element`

        :Returns:

                The second element of the data

        **Examples**

        >>> d = cf.Data([[1, 2], [3, 4]])
        >>> d.second_element()
        2
        >>> d[0, 1] = cf.masked
        >>> d.second_element()
        masked

        """
        if self.can_compute():
            return super().second_element()

        raise ValueError(
            "Second element of the data is considered too expensive "
            "to compute. Consider setting the 'force_compute' atribute, or "
            "setting the log level to 'DEBUG'."
        )

    def last_element(self):
        """Return the last element of the data as a scalar.

        If the value is deemed too expensive to compute then a
        `ValueError` is raised instead. It is considered acceptable to
        compute the value in the following circumstances:

        * The `force_compute` attribute is True.

        * The current log level is ``'DEBUG'``.

        * The stored computations consist only of initialisation,
          subspace or copy functions.

        .. versionadded:: 4.0.0

        .. seealso:: `first_element`, `second_element`

        :Returns:

                The last element of the data

        **Examples**

        >>> d = cf.Data([[1, 2], [3, 4]])
        >>> d.last_element()
        4
        >>> d[1, 1] = cf.masked
        >>> d.last_element()
        masked

        """
        if self.can_compute():
            return super().last_element()

        raise ValueError(
            "First element of the data is considered too expensive "
            "to compute. Consider setting the 'force_compute' attribute, or "
            "setting the log level to 'DEBUG'."
        )

    @daskified(_DASKIFIED_VERBOSE)
    def flat(self, ignore_masked=True):
        """Return a flat iterator over elements of the data array.

        **Performance**

        Any delayed operations and/or disk interactions will be
        executed during *each* iteration, possibly leading to poor
        performance. If possible, consider bringing the values into
        memory first with `persist` or using ``d.array.flat``.

        .. seealso:: `flatten`, `persist`

        :Parameters:

            ignore_masked: `bool`, optional
                If False then masked and unmasked elements will be
                returned. By default only unmasked elements are
                returned

        :Returns:

            generator
                An iterator over elements of the data array.

        **Examples**

        >>> d = cf.Data([[1, 2], [3,4]], mask=[[0, 1], [0, 0]])
        >>> print(d.array)
        [[1 --]
         [3 4]]
        >>> list(d.flat())
        [1, 3, 4]
        >>> list(d.flat(ignore_masked=False))
        [1, masked, 3, 4]

        """
        mask = self.mask

        if ignore_masked:
            for index in self.ndindex():
                if not mask[index]:
                    yield self[index].array.item()
        else:
            for index in self.ndindex():
                if not mask[index]:
                    yield self[index].array.item()
                else:
                    yield cf_masked

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def flatten(self, axes=None, inplace=False):
        """Flatten specified axes of the data.

        Any subset of the axes may be flattened.

        The shape of the data may change, but the size will not.

        The flattening is executed in row-major (C-style) order. For
        example, the array ``[[1, 2], [3, 4]]`` would be flattened across
        both dimensions to ``[1 2 3 4]``.

        .. versionadded:: 3.0.2

        .. seealso:: `compressed`, `flat`, `insert_dimension`, `flip`,
                     `swapaxes`, `transpose`

        :Parameters:

            axes: (sequence of) `int`
                Select the axes to be flattened. By default all axes
                are flattened. Each axis is identified by its integer
                position. No axes are flattened if *axes* is an empty
                sequence.

            {{inplace: `bool`, optional}}

        :Returns:

            `Data` or `None`
                The flattened data, or `None` if the operation was
                in-place.

        **Examples**

        >>> import numpy as np
        >>> d = cf.Data(np.arange(24).reshape(1, 2, 3, 4))
        >>> d
        <CF Data(1, 2, 3, 4): [[[[0, ..., 23]]]]>
        >>> print(d.array)
        [[[[ 0  1  2  3]
           [ 4  5  6  7]
           [ 8  9 10 11]]
          [[12 13 14 15]
           [16 17 18 19]
           [20 21 22 23]]]]

        >>> e = d.flatten()
        >>> e
        <CF Data(24): [0, ..., 23]>
        >>> print(e.array)
        [ 0  1  2  3  4  5  6  7  8  9 10 11 12 13 14 15 16 17 18 19 20 21 22 23]

        >>> e = d.flatten([])
        >>> e
        <CF Data(1, 2, 3, 4): [[[[0, ..., 23]]]]>

        >>> e = d.flatten([1, 3])
        >>> e
        <CF Data(1, 8, 3): [[[0, ..., 23]]]>
        >>> print(e.array)
        [[[ 0  4  8]
          [ 1  5  9]
          [ 2  6 10]
          [ 3  7 11]
          [12 16 20]
          [13 17 21]
          [14 18 22]
          [15 19 23]]]

        >>> d.flatten([0, -1], inplace=True)
        >>> d
        <CF Data(4, 2, 3): [[[0, ..., 23]]]>
        >>> print(d.array)
        [[[ 0  4  8]
          [12 16 20]]
         [[ 1  5  9]
          [13 17 21]]
         [[ 2  6 10]
          [14 18 22]]
         [[ 3  7 11]
          [15 19 23]]]

        """
        d = _inplace_enabled_define_and_cleanup(self)

        ndim = d.ndim
        if not ndim:
            if axes or axes == 0:
                raise ValueError(
                    "Can't flatten: Can't remove axes from "
                    f"scalar {self.__class__.__name__}"
                )

            return d

        if axes is None:
            axes = list(range(ndim))
        else:
            axes = sorted(d._parse_axes(axes))

        n_axes = len(axes)
        if n_axes <= 1:
            return d

        dx = d.to_dask_array()

        # It is important that the first axis in the list is the
        # left-most flattened axis.
        #
        # E.g. if the shape is (10, 20, 30, 40, 50, 60) and the axes
        #      to be flattened are [2, 4], then the data must be
        #      transposed with order [0, 1, 2, 4, 3, 5]
        order = [i for i in range(ndim) if i not in axes]
        order[axes[0] : axes[0]] = axes
        dx = dx.transpose(order)

        # Find the flattened shape.
        #
        # E.g. if the *transposed* shape is (10, 20, 30, 50, 40, 60)
        #      and *transposed* axes [2, 3] are to be flattened then
        #      the new shape will be (10, 20, 1500, 40, 60)
        shape = d.shape
        new_shape = [n for i, n in enumerate(shape) if i not in axes]
        new_shape.insert(axes[0], reduce(mul, [shape[i] for i in axes], 1))

        dx = dx.reshape(new_shape)
        d._set_dask(dx)

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_deprecated_kwarg_check("i")
    @_inplace_enabled(default=False)
    def floor(self, inplace=False, i=False):
        """Return the floor of the data array.

        .. versionadded:: 1.0

        .. seealso:: `ceil`, `rint`, `trunc`

        :Parameters:

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`

        **Examples**

        >>> d = cf.Data([-1.9, -1.5, -1.1, -1, 0, 1, 1.1, 1.5 , 1.9])
        >>> print(d.array)
        [-1.9 -1.5 -1.1 -1.   0.   1.   1.1  1.5  1.9]
        >>> print(d.floor().array)
        [-2. -2. -2. -1.  0.  1.  1.  1.  1.]

        """
        d = _inplace_enabled_define_and_cleanup(self)
        dx = d.to_dask_array()
        d._set_dask(da.floor(dx))
        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    @_deprecated_kwarg_check("i")
    def outerproduct(self, a, inplace=False, i=False):
        """Compute the outer product with another data array.

        The axes of result will be the combined axes of the two input
        arrays.

        .. seealso:: `np.multiply.outer`

        :Parameters:

            a: array_like
                The data with which to form the outer product.

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`
                The outer product, or `None` if the operation was
                in-place.

        **Examples**

        >>> d = cf.Data([1, 2, 3], 'm')
        >>> d
        <CF Data(3): [1, 2, 3] m>
        >>> f = d.outerproduct([4, 5, 6, 7])
        >>> f
        <CF Data(3, 4): [[4, ..., 21]] m>
        >>> print(f.array)
        [[ 4  5  6  7]
         [ 8 10 12 14]
         [12 15 18 21]]

        >>> e = cf.Data([[4, 5, 6, 7], [6, 7, 8, 9]], 's-1')
        >>> e
        <CF Data(2, 4): [[4, ..., 9]] s-1>
        >>> f = d.outerproduct(e)
        >>> f
        <CF Data(3, 2, 4): [[[4, ..., 27]]] m.s-1>
        >>> print(f.array)
        [[[ 4  5  6  7]
          [ 6  7  8  9]]

         [[ 8 10 12 14]
          [12 14 16 18]]

         [[12 15 18 21]
          [18 21 24 27]]]

        """
        d = _inplace_enabled_define_and_cleanup(self)

        # Cast 'a' as a Data object so that it definitely has sensible
        # Units
        a = self.asdata(a)
        try:
            a = conform_units(a, d.Units)
        except ValueError:
            pass

        dx = d.to_dask_array()
        dx = da.ufunc.multiply.outer(dx, a)
        d._set_dask(dx)

        d.override_units(d.Units * a.Units, inplace=True)

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_deprecated_kwarg_check("i")
    @_inplace_enabled(default=False)
    def change_calendar(self, calendar, inplace=False, i=False):
        """Change the calendar of date-time array elements.

        Reinterprets the existing date-times for the new calendar by
        adjusting the underlying numerical values relative to the
        reference date-time defined by the units.

        If a date-time value is not allowed in the new calendar then
        an exception is raised when the data array is accessed.

        .. seealso:: `override_calendar`, `Units`

        :Parameters:

            calendar: `str`
                The new calendar, as recognised by the CF conventions.

                *Parameter example:*
                  ``'proleptic_gregorian'``

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`
                The new data with updated calendar, or `None` if the
                operation was in-place.

        **Examples**

        >>> d = cf.Data([0, 1, 2, 3, 4], 'days since 2004-02-27')
        >>> print(d.array)
        [0 1 2 3 4]
        >>> print(d.datetime_as_string)
        ['2004-02-27 00:00:00' '2004-02-28 00:00:00' '2004-02-29 00:00:00'
         '2004-03-01 00:00:00' '2004-03-02 00:00:00']
        >>> e = d.change_calendar('360_day')
        >>> print(e.array)
        [0 1 2 4 5]
        >>> print(e.datetime_as_string)
        ['2004-02-27 00:00:00' '2004-02-28 00:00:00' '2004-02-29 00:00:00'
        '2004-03-01 00:00:00' '2004-03-02 00:00:00']

        >>> d.change_calendar('noleap').array
        Traceback (most recent call last):
            ...
        ValueError: invalid day number provided in cftime.DatetimeNoLeap(2004, 2, 29, 0, 0, 0, 0, has_year_zero=True)

        """
        d = _inplace_enabled_define_and_cleanup(self)

        units = self.Units
        if not units.isreftime:
            raise ValueError(
                "Can't change calendar of non-reference time "
                f"units: {units!r}"
            )

        d._asdatetime(inplace=True)
        d.override_calendar(calendar, inplace=True)
        d._asreftime(inplace=True)

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_deprecated_kwarg_check("i")
    @_inplace_enabled(default=False)
    def override_units(self, units, inplace=False, i=False):
        """Override the data array units.

        Not to be confused with setting the `Units` attribute to units
        which are equivalent to the original units. This is different
        because in this case the new units need not be equivalent to the
        original ones and the data array elements will not be changed to
        reflect the new units.

        :Parameters:

            units: `str` or `Units`
                The new units for the data array.

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`
                The new data, or `None` if the operation was in-place.

        **Examples**

        >>> d = cf.Data(1012.0, 'hPa')
        >>> e = d.override_units('km')
        >>> e.Units
        <Units: km>
        >>> e.datum()
        1012.0
        >>> d.override_units(cf.Units('watts'), inplace=True)
        >>> d.Units
        <Units: watts>
        >>> d.datum()
        1012.0

        """
        d = _inplace_enabled_define_and_cleanup(self)
        d._Units = Units(units)
        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_deprecated_kwarg_check("i")
    @_inplace_enabled(default=False)
    def override_calendar(self, calendar, inplace=False, i=False):
        """Override the calendar of the data array elements.

        Not to be confused with using the `change_calendar` method or
        setting the `d.Units.calendar`. `override_calendar` is different
        because the new calendar need not be equivalent to the original
        ones and the data array elements will not be changed to reflect
        the new units.

        :Parameters:

            calendar: `str`
                The new calendar.

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`
                The new data, or `None` if the operation was in-place.

        **Examples**

        >>> d = cf.Data(1, 'days since 2020-02-28')
        >>> d
        <CF Data(): 2020-02-29 00:00:00>
        >>> d.datum()
        1
        >>> e = d.override_calendar('noleap')
        <CF Data(): 2020-03-01 00:00:00 noleap>
        >>> e.datum()
        1

        """
        d = _inplace_enabled_define_and_cleanup(self)
        d._Units = Units(d.Units._units, calendar)
        return d

    def to_dask_array(self, apply_mask_hardness=False):
        """Convert the data to a `dask` array.

        .. warning:: By default, the mask hardness of the returned
                     dask array might not be the same as that
                     specified by the `hardmask` attribute.

                     This could cause problems if a subsequent
                     operation on the returned dask array involves the
                     un-masking of masked values (such as by indexed
                     assignment).

                     To guarantee that the mask hardness of the
                     returned dassk array is correct, set the
                     *apply_mask_hardness* parameter to True.

        .. versionadded:: TODODASKVER

        :Parameters:

            apply_mask_hardness: `bool`, optional
                If True then force the mask hardness of the returned
                array to be that given by the `hardmask` attribute.

        :Returns:

            `dask.array.Array`
                The dask array contained within the `Data` instance.

        **Examples**

        >>> d = cf.Data([1, 2, 3, 4], 'm')
        >>> dx = d.to_dask_array()
        >>> dx
        >>> dask.array<array, shape=(4,), dtype=int64, chunksize=(4,), chunktype=numpy.ndarray>
        >>> dask.array.asanyarray(d) is dx
        True

        >>> d.to_dask_array(apply_mask_hardness=True)
        dask.array<cf_harden_mask, shape=(4,), dtype=int64, chunksize=(4,), chunktype=numpy.ndarray>

        >>> d = cf.Data([1, 2, 3, 4], 'm', hardmask=False)
        >>> d.to_dask_array(apply_mask_hardness=True)
        dask.array<cf_soften_mask, shape=(4,), dtype=int64, chunksize=(4,), chunktype=numpy.ndarray>

        """
        if apply_mask_hardness:
            if self.hardmask:
                self.harden_mask()
            else:
                self.soften_mask()

        return self._custom["dask"]

    @daskified(_DASKIFIED_VERBOSE)
    def datum(self, *index):
        """Return an element of the data array as a standard Python
        scalar.

        The first and last elements are always returned with
        ``d.datum(0)`` and ``d.datum(-1)`` respectively, even if the data
        array is a scalar array or has two or more dimensions.

        The returned object is of the same type as is stored internally.

        .. seealso:: `array`, `datetime_array`

        :Parameters:

            index: *optional*
                Specify which element to return. When no positional
                arguments are provided, the method only works for data
                arrays with one element (but any number of dimensions),
                and the single element is returned. If positional
                arguments are given then they must be one of the
                fdlowing:

                * An integer. This argument is interpreted as a flat index
                  into the array, specifying which element to copy and
                  return.

                  *Parameter example:*
                    If the data array shape is ``(2, 3, 6)`` then:
                    * ``d.datum(0)`` is equivalent to ``d.datum(0, 0, 0)``.
                    * ``d.datum(-1)`` is equivalent to ``d.datum(1, 2, 5)``.
                    * ``d.datum(16)`` is equivalent to ``d.datum(0, 2, 4)``.

                  If *index* is ``0`` or ``-1`` then the first or last data
                  array element respectively will be returned, even if the
                  data array is a scalar array.

                * Two or more integers. These arguments are interpreted as a
                  multidimensional index to the array. There must be the
                  same number of integers as data array dimensions.

                * A tuple of integers. This argument is interpreted as a
                  multidimensional index to the array. There must be the
                  same number of integers as data array dimensions.

                  *Parameter example:*
                    ``d.datum((0, 2, 4))`` is equivalent to ``d.datum(0,
                    2, 4)``; and ``d.datum(())`` is equivalent to
                    ``d.datum()``.

        :Returns:

                A copy of the specified element of the array as a suitable
                Python scalar.

        **Examples**

        >>> d = cf.Data(2)
        >>> d.datum()
        2
        >>> 2 == d.datum(0) == d.datum(-1) == d.datum(())
        True

        >>> d = cf.Data([[2]])
        >>> 2 == d.datum() == d.datum(0) == d.datum(-1)
        True
        >>> 2 == d.datum(0, 0) == d.datum((-1, -1)) == d.datum(-1, 0)
        True

        >>> d = cf.Data([[4, 5, 6], [1, 2, 3]], 'metre')
        >>> d[0, 1] = cf.masked
        >>> print(d)
        [[4 -- 6]
         [1  2 3]]
        >>> d.datum(0)
        4
        >>> d.datum(-1)
        3
        >>> d.datum(1)
        masked
        >>> d.datum(4)
        2
        >>> d.datum(-2)
        2
        >>> d.datum(0, 0)
        4
        >>> d.datum(-2, -1)
        6
        >>> d.datum(1, 2)
        3
        >>> d.datum((0, 2))
        6

        """
        # TODODASKAPI: consider renaming/aliasing to 'item'. Might depend
        # on whether or not the APIs are the same.

        if index:
            n_index = len(index)
            if n_index == 1:
                index = index[0]
                if index == 0:
                    # This also works for scalar arrays
                    index = (slice(0, 1),) * self.ndim
                elif index == -1:
                    # This also works for scalar arrays
                    index = (slice(-1, None),) * self.ndim
                elif isinstance(index, int):
                    if index < 0:
                        index += self._size

                    index = np.unravel_index(index, self.shape)
                elif len(index) == self.ndim:
                    index = tuple(index)
                else:
                    raise ValueError(
                        f"Incorrect number of indices ({n_index}) for "
                        f"{self.ndim}-d {self.__class__.__name__} data"
                    )
            elif n_index != self.ndim:
                raise ValueError(
                    f"Incorrect number of indices ({n_index}) for "
                    f"{self.ndim}-d {self.__class__.__name__} data"
                )

            array = self[index].array

        elif self.size == 1:
            array = self.array

        else:
            raise ValueError(
                f"For size {self.size} data, must provide an index of "
                "the element to be converted to a Python scalar"
            )

        if not np.ma.isMA(array):
            return array.item()

        mask = array.mask
        if mask is np.ma.nomask or not mask.item():
            return array.item()

        return cf_masked

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def masked_invalid(self, inplace=False):
        """Mask the array where invalid values occur (NaN or inf).

        .. seealso:: `where`

        :Parameters:

            {{inplace: `bool`, optional}}

        :Returns:

            `Data` or `None`
                The masked data, or `None` if the operation was
                in-place.

        **Examples**

        >>> d = cf.Data([0, 1, 2])
        >>> e = cf.Data([0, 2, 0])
        >>> f = d / e
        >>> f
        <CF Data(3): [nan, 0.5, inf]>
        >>> f.masked_invalid()
        <CF Data(3): [--, 0.5, --]>

        """
        d = _inplace_enabled_define_and_cleanup(self)
        dx = self.to_dask_array()
        dx = da.ma.masked_invalid(dx)
        d._set_dask(dx)
        return d

    def del_calendar(self, default=ValueError()):
        """Delete the calendar.

        .. seealso:: `get_calendar`, `has_calendar`, `set_calendar`,
                     `del_units`, `Units`

        :Parameters:

            default: optional
                Return the value of the *default* parameter if the
                calendar has not been set.

                {{default Exception}}

        :Returns:

            `str`
                The value of the deleted calendar.

        **Examples**

        >>> d = cf.Data(1, "days since 2000-1-1", calendar="noleap")
        >>> d.del_calendar()
        'noleap'
        >>> print(d.del_calendar())
        None

        >>> d = cf.Data(1, "days since 2000-1-1")
        >>> print(d.del_calendar())
        None

        >>> d = cf.Data(1, "m")
        Traceback (most recent call last):
            ...
        ValueError: Units <Units: m> have no calendar

        """
        units = self.Units
        if not units.isreftime:
            return self._default(default, f"Units {units!r} have no calendar")

        calendar = getattr(units, "calendar", None)
        if calendar is None:
            return self._default(
                default, f"{self.__class__.__name__} has no calendar"
            )

        self.override_calendar(None, inplace=True)
        return calendar

    def del_units(self, default=ValueError()):
        """Delete the units.

        .. seealso:: `get_units`, `has_units`, `set_units`,
                     `del_calendar`, `Units`

        :Parameters:

            default: optional
                Return the value of the *default* parameter if the units
                has not been set.

                {{default Exception}}

        :Returns:

            `str`
                The value of the deleted units.

        **Examples**

        >>> d = cf.Data(1, "m")
        >>> d.del_units()
        'm'
        >>> d.Units
        <Units: >
        >>> d.del_units()
        Traceback (most recent call last):
            ...
        ValueError: Data has no units

        >>> d = cf.Data(1, "days since 2000-1-1", calendar="noleap")
        >>> d.del_units()
        'days since 2000-1-1'
        >>> d.Units
        <Units: noleap>

        """
        u = self.Units
        units = getattr(u, "units", None)
        calendar = getattr(u, "calendar", None)
        self.override_units(Units(None, calendar), inplace=True)

        if units is not None:
            return units

        return self._default(
            default, f"{self.__class__.__name__} has no units"
        )

    @classmethod
    def masked_all(
        cls,
        shape,
        dtype=None,
        units=None,
        calendar=None,
        chunks=_DEFAULT_CHUNKS,
    ):
        """Return an empty masked array with all elements masked.

        .. seealso:: `empty`, `ones`, `zeros`, `masked_invalid`

        :Parameters:

            shape: `int` or `tuple` of `int`
                The shape of the new array. e.g. ``(2, 3)`` or ``2``.

            dtype: data-type
                The desired output data-type for the array, e.g.
                `numpy.int8`. The default is `numpy.float64`.

            units: `str` or `Units`
                The units for the new data array.

            calendar: `str`, optional
                The calendar for reference time units.

            {{chunks: `int`, `tuple`, `dict` or `str`, optional}}

                .. versionadded:: 4.0.0

        :Returns:

            `Data`
                A masked array with all data masked.

        **Examples**

        >>> d = cf.Data.masked_all((2, 2))
        >>> print(d.array)
        [[-- --]
         [-- --]]

        >>> d = cf.Data.masked_all((), dtype=bool)
        >>> d.array
        masked_array(data=--,
                     mask=True,
               fill_value=True,
                    dtype=bool)

        """
        d = cls.empty(
            shape=shape,
            dtype=dtype,
            units=units,
            calendar=calendar,
            chunks=chunks,
        )
        dx = d.to_dask_array()
        dx = dx.map_blocks(partial(np.ma.array, mask=True, copy=False))
        d._set_dask(dx)
        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    @_deprecated_kwarg_check("i")
    def mid_range(
        self,
        axes=None,
        squeeze=False,
        mtol=1,
        split_every=None,
        inplace=False,
        i=False,
    ):
        """Calculate mid-range values.

        The mid-range is half of the maximum plus the minimum.

        Calculates the mid-range value or the mid-range values along
        axes.

        See
        https://ncas-cms.github.io/cf-python/analysis.html#collapse-methods
        for mathematical definitions.

         ..seealso:: `sample_size`, `max`, `min`, `range`

        :Parameters:

            {{collapse axes: (sequence of) `int`, optional}}

            {{collapse squeeze: `bool`, optional}}

            {{mtol: number, optional}}

            {{split_every: `int` or `dict`, optional}}

                .. versionadded:: TODODASKVER

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`
                The collapsed array.

        **Examples**

        >>> a = np.ma.arange(12).reshape(4, 3)
        >>> d = cf.Data(a, 'K')
        >>> d[1, 1] = cf.masked
        >>> print(d.array)
        [[0 1 2]
         [3 -- 5]
         [6 7 8]
         [9 10 11]]
        >>> d.mid_range()
        <CF Data(1, 1): [[5.5]] K>

        """
        d = _inplace_enabled_define_and_cleanup(self)
        d, _ = _collapse(
            Collapse.mid_range,
            d,
            axis=axes,
            keepdims=not squeeze,
            split_every=split_every,
            mtol=mtol,
        )
        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_deprecated_kwarg_check("i")
    @_inplace_enabled(default=False)
    def flip(self, axes=None, inplace=False, i=False):
        """Reverse the direction of axes of the data array.

        .. seealso:: `flatten', `insert_dimension`, `squeeze`, `swapaxes`,
                     `transpose`

        :Parameters:

            axes: (sequence of) `int`
                Select the axes. By default all axes are flipped. Each
                axis is identified by its integer position. No axes
                are flipped if *axes* is an empty sequence.

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`

        **Examples**

        >>> d.flip()
        >>> d.flip(1)
        >>> d.flip([0, 1])
        >>> d.flip([])

        >>> e = d[::-1, :, ::-1]
        >>> d.flip((2, 0)).equals(e)
        True

        """
        d = _inplace_enabled_define_and_cleanup(self)

        if axes is not None and not axes and axes != 0:  # i.e. empty sequence
            return d

        if axes is None:
            iaxes = range(d.ndim)
        else:
            iaxes = d._parse_axes(axes)

        if not iaxes:
            return d

        index = [
            slice(None, None, -1) if i in iaxes else slice(None)
            for i in range(d.ndim)
        ]

        dx = d.to_dask_array()
        dx = dx[tuple(index)]
        d._set_dask(dx)

        return d

    @daskified(_DASKIFIED_VERBOSE)
    def inspect(self):
        """Inspect the object for debugging.

        .. seealso:: `cf.inspect`

        :Returns:

            `None`

        **Examples**

        >>> d = cf.Data([9], 'm')
        >>> d.inspect()
        <CF Data(1): [9] m>
        -------------------
        {'_components': {'custom': {'_Units': <Units: m>,
                                    '_axes': ('dim0',),
                                    '_cyclic': set(),
                                    '_hardmask': True,
                                    'dask': dask.array<cf_harden_mask, shape=(1,), dtype=int64, chunksize=(1,), chunktype=numpy.ndarray>},
                         'netcdf': {}}}

        """
        from ..functions import inspect

        inspect(self)

    @daskified(_DASKIFIED_VERBOSE)
    def isclose(self, y, rtol=None, atol=None):
        """Return where data are element-wise equal within a tolerance.

        {{equals tolerance}}

        For numeric data arrays, ``d.isclose(e, rtol, atol)`` is
        equivalent to ``abs(d - e) <= atol + rtol*abs(e)``,
        otherwise it is equivalent to ``d == e``.

        :Parameters:

            y: data_like
                The array to compare.

            atol: `float`, optional
                The absolute tolerance for all numerical comparisons. By
                default the value returned by the `atol` function is used.

            rtol: `float`, optional
                The relative tolerance for all numerical comparisons. By
                default the value returned by the `rtol` function is used.

        :Returns:

             `bool`
                 A boolean array of where the data are close to *y*.

        **Examples**

        >>> d = cf.Data([1000, 2500], 'metre')
        >>> e = cf.Data([1, 2.5], 'km')
        >>> print(d.isclose(e).array)
        [ True  True]

        >>> d = cf.Data(['ab', 'cdef'])
        >>> print(d.isclose([[['ab', 'cdef']]]).array)
        [[[ True  True]]]

        >>> d = cf.Data([[1000, 2500], [1000, 2500]], 'metre')
        >>> e = cf.Data([1, 2.5], 'km')
        >>> print(d.isclose(e).array)
        [[ True  True]
         [ True  True]]

        >>> d = cf.Data([1, 1, 1], 's')
        >>> print(d.isclose(1).array)
        [ True  True  True]

        """
        a = np.empty((), dtype=self.dtype)
        b = np.empty((), dtype=da.asanyarray(y).dtype)
        try:
            # Check if a numerical isclose is possible
            np.isclose(a, b)
        except TypeError:
            # self and y do not have suitable numeric data types
            # (e.g. both are strings)
            return self == y
        else:
            # self and y have suitable numeric data types
            if atol is None:
                atol = self._atol

            if rtol is None:
                rtol = self._rtol

            y = conform_units(y, self.Units)

            dx = da.isclose(self, y, atol=atol, rtol=rtol)

            d = self.copy(array=False)
            d._set_dask(dx)
            d.hardmask = _DEFAULT_HARDMASK
            d.override_units(_units_None, inplace=True)
            return d

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def reshape(self, *shape, merge_chunks=True, limit=None, inplace=False):
        """Change the shape of the data without changing its values.

        It assumes that the array is stored in row-major order, and
        only allows for reshapings that collapse or merge dimensions
        like ``(1, 2, 3, 4) -> (1, 6, 4)`` or ``(64,) -> (4, 4, 4)``.

        :Parameters:

            shape: `tuple` of `int`, or any number of `int`
                The new shape for the data, which should be compatible
                with the original shape. If an integer, then the
                result will be a 1-d array of that length. One shape
                dimension can be -1, in which case the value is
                inferred from the length of the array and remaining
                dimensions.

            merge_chunks: `bool`
                When True (the default) merge chunks using the logic
                in `dask.array.rechunk` when communication is
                necessary given the input array chunking and the
                output shape. When False, the input array will be
                rechunked to a chunksize of 1, which can create very
                many tasks. See `dask.array.reshape` for details.

            limit: int, optional
                The maximum block size to target in bytes. If no limit
                is provided, it defaults to a size in bytes defined by
                the `cf.chunksize` function.

        :Returns:

            `Data` or `None`
                 The reshaped data, or `None` if the operation was
                 in-place.

        **Examples**

        >>> d = cf.Data(np.arange(12))
        >>> print(d.array)
        [ 0  1  2  3  4  5  6  7  8  9 10 11]
        >>> print(d.reshape(3, 4).array)
        [[ 0  1  2  3]
         [ 4  5  6  7]
         [ 8  9 10 11]]
        >>> print(d.reshape((4, 3)).array)
        [[ 0  1  2]
         [ 3  4  5]
         [ 6  7  8]
         [ 9 10 11]]
        >>> print(d.reshape(-1, 6).array)
        [[ 0  1  2  3  4  5]
         [ 6  7  8  9 10 11]]
        >>>  print(d.reshape(1, 1, 2, 6).array)
        [[[[ 0  1  2  3  4  5]
           [ 6  7  8  9 10 11]]]]
        >>> print(d.reshape(1, 1, -1).array)
        [[[[ 0  1  2  3  4  5  6  7  8  9 10 11]]]]

        """
        d = _inplace_enabled_define_and_cleanup(self)
        dx = d.to_dask_array()
        dx = dx.reshape(*shape, merge_chunks=merge_chunks, limit=limit)
        d._set_dask(dx)
        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_deprecated_kwarg_check("i")
    @_inplace_enabled(default=False)
    def rint(self, inplace=False, i=False):
        """Round the data to the nearest integer, element-wise.

        .. versionadded:: 1.0

        .. seealso:: `ceil`, `floor`, `trunc`

        :Parameters:

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`
                The rounded data. If the operation was in-place then
                `None` is returned.

        **Examples**

        >>> d = cf.Data([-1.9, -1.5, -1.1, -1, 0, 1, 1.1, 1.5 , 1.9])
        >>> print(d.array)
        [-1.9 -1.5 -1.1 -1.   0.   1.   1.1  1.5  1.9]
        >>> print(d.rint().array)
        [-2. -2. -1. -1.  0.  1.  1.  2.  2.]

        """
        d = _inplace_enabled_define_and_cleanup(self)
        dx = d.to_dask_array()
        d._set_dask(da.rint(dx))
        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def root_mean_square(
        self,
        axes=None,
        squeeze=False,
        mtol=1,
        weights=None,
        split_every=None,
        inplace=False,
    ):
        """Calculate root mean square (RMS) values.

        Calculates the RMS value or the RMS values along axes.

        See
        https://ncas-cms.github.io/cf-python/analysis.html#collapse-methods
        for mathematical definitions.

         ..seealso:: `sample_size`, `mean`, `sum`,

        :Parameters:

            {{collapse axes: (sequence of) `int`, optional}}

            {{weights: data_like, `dict`, or `None`, optional}}

            {{collapse squeeze: `bool`, optional}}

            {{mtol: number, optional}}

            {{split_every: `int` or `dict`, optional}}

                .. versionadded:: TODODASKVER

            {{inplace: `bool`, optional}}

        :Returns:

            `Data` or `None`
                The collapsed array.

        **Examples**

        >>> a = np.ma.arange(12).reshape(4, 3)
        >>> d = cf.Data(a, 'K')
        >>> d[1, 1] = cf.masked
        >>> print(d.array)
        [[0 1 2]
         [3 -- 5]
         [6 7 8]
         [9 10 11]]
        >>> d.root_mean_square()
        <CF Data(1, 1): [[6.674238124719146]] K>

        >>> w = np.linspace(1, 2, 3)
        >>> print(w)
        [1.  1.5 2. ]
        >>> d.root_mean_square(weights=w)
        <CF Data(1, 1): [[6.871107713616576]] K>

        """
        d = _inplace_enabled_define_and_cleanup(self)
        d, _ = _collapse(
            Collapse.rms,
            d,
            axis=axes,
            weights=weights,
            keepdims=not squeeze,
            split_every=split_every,
            mtol=mtol,
        )
        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_deprecated_kwarg_check("i")
    @_inplace_enabled(default=False)
    def round(self, decimals=0, inplace=False, i=False):
        """Evenly round elements of the data array to the given number
        of decimals.

        Values exactly halfway between rounded decimal values are rounded
        to the nearest even value. Thus 1.5 and 2.5 round to 2.0, -0.5 and
        0.5 round to 0.0, etc. Results may also be surprising due to the
        inexact representation of decimal fractions in the IEEE floating
        point standard and errors introduced when scaling by powers of
        ten.

        .. versionadded:: 1.1.4

        .. seealso:: `ceil`, `floor`, `rint`, `trunc`

        :Parameters:

            decimals : `int`, optional
                Number of decimal places to round to (default: 0). If
                decimals is negative, it specifies the number of positions
                to the left of the decimal point.

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`

        **Examples**

        >>> d = cf.Data([-1.81, -1.41, -1.01, -0.91, 0.09, 1.09, 1.19, 1.59, 1.99])
        >>> print(d.array)
        [-1.81 -1.41 -1.01 -0.91  0.09  1.09  1.19  1.59  1.99]
        >>> print(d.round().array)
        [-2., -1., -1., -1.,  0.,  1.,  1.,  2.,  2.]
        >>> print(d.round(1).array)
        [-1.8, -1.4, -1. , -0.9,  0.1,  1.1,  1.2,  1.6,  2. ]
        >>> print(d.round(-1).array)
        [-0., -0., -0., -0.,  0.,  0.,  0.,  0.,  0.]

        """
        d = _inplace_enabled_define_and_cleanup(self)
        dx = d.to_dask_array()
        d._set_dask(da.round(dx, decimals=decimals))
        return d

    def stats(
        self,
        all=False,
        minimum=True,
        mean=True,
        median=True,
        maximum=True,
        range=True,
        mid_range=True,
        standard_deviation=True,
        root_mean_square=True,
        sample_size=True,
        minimum_absolute_value=False,
        maximum_absolute_value=False,
        mean_absolute_value=False,
        mean_of_upper_decile=False,
        sum=False,
        sum_of_squares=False,
        variance=False,
        weights=None,
    ):
        """Calculate statistics of the data.

        By default the minimum, mean, median, maximum, range, mid-range,
        standard deviation, root mean square, and sample size are
        calculated. But this selection may be edited, and other metrics
        are available.

        .. seealso:: `minimum`, `mean`, `median`, `maximum`, `range`,
                     `mid_range`, `standard_deviation`,
                     `root_mean_square`, `sample_size`,
                     `minimum_absolute_value`, `maximum_absolute_value`,
                     `mean_absolute_value`, `mean_of_upper_decile`, `sum`,
                     `sum_of_squares`, `variance`

        :Parameters:

            all: `bool`, optional
                Calculate all possible statistics, regardless of the value
                of individual metric parameters.

            minimum: `bool`, optional
                Calculate the minimum of the values.

            maximum: `bool`, optional
                Calculate the maximum of the values.

            maximum_absolute_value: `bool`, optional
                Calculate the maximum of the absolute values.

            minimum_absolute_value: `bool`, optional
                Calculate the minimum of the absolute values.

            mid_range: `bool`, optional
                Calculate the average of the maximum and the minimum of
                the values.

            median: `bool`, optional
                Calculate the median of the values.

            range: `bool`, optional
                Calculate the absolute difference between the maximum and
                the minimum of the values.

            sum: `bool`, optional
                Calculate the sum of the values.

            sum_of_squares: `bool`, optional
                Calculate the sum of the squares of values.

            sample_size: `bool`, optional
                Calculate the sample size, i.e. the number of non-missing
                values.

            mean: `bool`, optional
                Calculate the weighted or unweighted mean of the values.

            mean_absolute_value: `bool`, optional
                Calculate the mean of the absolute values.

            mean_of_upper_decile: `bool`, optional
                Calculate the mean of the upper group of data values
                defined by the upper tenth of their distribution.

            variance: `bool`, optional
                Calculate the weighted or unweighted variance of the
                values, with a given number of degrees of freedom.

            standard_deviation: `bool`, optional
                Calculate the square root of the weighted or unweighted
                variance.

            root_mean_square: `bool`, optional
                Calculate the square root of the weighted or unweighted
                mean of the squares of the values.

            {{weights: data_like, `dict`, or `None`, optional}}

        :Returns:

            `dict`
                The statistics.

        **Examples**

        >>> d = cf.Data([[0, 1, 2], [3, -99, 5]], mask=[[0, 0, 0], [0, 1, 0]])
        >>> print(d.array)
        [[0  1  2]
         [3 --  5]]
        >>> d.stats()
        {'minimum': <CF Data(): 0>,
         'mean': <CF Data(): 2.2>,
         'median': <CF Data(): 2.0>,
         'maximum': <CF Data(): 5>,
         'range': <CF Data(): 5>,
         'mid_range': <CF Data(): 2.5>,
         'standard_deviation': <CF Data(): 1.7204650534085253>,
         'root_mean_square': <CF Data(): 2.792848008753788>,
         'sample_size': 5}
        >>> d.stats(all=True)
        {'minimum': <CF Data(): 0>,
         'mean': <CF Data(): 2.2>,
         'median': <CF Data(): 2.0>,
         'maximum': <CF Data(): 5>,
         'range': <CF Data(): 5>,
         'mid_range': <CF Data(): 2.5>,
         'standard_deviation': <CF Data(): 1.7204650534085253>,
         'root_mean_square': <CF Data(): 2.792848008753788>,
         'minimum_absolute_value': <CF Data(): 0>,
         'maximum_absolute_value': <CF Data(): 5>,
         'mean_absolute_value': <CF Data(): 2.2>,
         'mean_of_upper_decile': <CF Data(): 5.0>,
         'sum': <CF Data(): 11>,
         'sum_of_squares': <CF Data(): 39>,
         'variance': <CF Data(): 2.96>,
         'sample_size': 5}
        >>> d.stats(mean_of_upper_decile=True, range=False)
        {'minimum': <CF Data(): 0>,
         'mean': <CF Data(): 2.2>,
         'median': <CF Data(): 2.0>,
         'maximum': <CF Data(): 5>,
         'mid_range': <CF Data(): 2.5>,
         'standard_deviation': <CF Data(): 1.7204650534085253>,
         'root_mean_square': <CF Data(): 2.792848008753788>,
         'mean_of_upper_decile': <CF Data(): 5.0>,
         'sample_size': 5}

        """
        no_weights = (
            "minimum",
            "median",
            "maximum",
            "range",
            "mid_range",
            "minimum_absolute_value",
            "maximum_absolute_value",
            "sum",
            "sum_of_squares",
        )

        out = {}
        for stat in (
            "minimum",
            "mean",
            "median",
            "maximum",
            "range",
            "mid_range",
            "standard_deviation",
            "root_mean_square",
            "minimum_absolute_value",
            "maximum_absolute_value",
            "mean_absolute_value",
            "mean_of_upper_decile",
            "sum",
            "sum_of_squares",
            "variance",
        ):
            if all or locals()[stat]:
                func = getattr(self, stat)
                if stat in no_weights:
                    value = func(squeeze=True)
                else:
                    value = func(squeeze=True, weights=weights)

                out[stat] = value

        if all or sample_size:
            out["sample_size"] = int(self.sample_size())

        return out

    @daskified(_DASKIFIED_VERBOSE)
    @_deprecated_kwarg_check("i")
    @_inplace_enabled(default=False)
    def swapaxes(self, axis0, axis1, inplace=False, i=False):
        """Interchange two axes of an array.

        .. seealso:: `flatten', `flip`, 'insert_dimension`, `squeeze`,
                     `transpose`

        :Parameters:

            axis0, axis1 : `int`, `int`
                Select the axes to swap. Each axis is identified by its
                original integer position.

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`
                The data with swapped axis positions.

        **Examples**

        >>> d = cf.Data([[[1, 2, 3], [4, 5, 6]]])
        >>> d
        <CF Data(1, 2, 3): [[[1, ..., 6]]]>
        >>> d.swapaxes(1, 0)
        <CF Data(2, 1, 3): [[[1, ..., 6]]]>
        >>> d.swapaxes(0, -1)
        <CF Data(3, 2, 1): [[[1, ..., 6]]]>
        >>> d.swapaxes(1, 1)
        <CF Data(1, 2, 3): [[[1, ..., 6]]]>
        >>> d.swapaxes(-1, -1)
        <CF Data(1, 2, 3): [[[1, ..., 6]]]>

        """
        d = _inplace_enabled_define_and_cleanup(self)
        dx = self.to_dask_array()
        dx = da.swapaxes(dx, axis0, axis1)
        d._set_dask(dx)
        return d

    def fits_in_memory(self):
        """Return True if the array is small enough to be retained in
        memory.

        Returns True if the size of the array with all delayed
        operations computed, always including space for a full boolean
        mask, is small enough to be retained in available memory.

        .. note:: The delayed operations are actually not computed by
                  `fits_in_memory`, so it is possible that an
                  intermediate operation may require more than the
                  available memory, even if the final array does not.

        .. seealso:: `array`, `compute`, `nbytes`, `persist`,
                     `cf.free_memory`

        :Parameters:

            itemsize: deprecated at version TODODASKVER
                The number of bytes per word of the master data array.

        :Returns:

            `bool`
                Whether or not the computed array fits in memory.

        **Examples**

        >>> d = cf.Data([1], 'm')
        >>> d.fits_in_memory()
        True

        Create a double precision (8 bytes per word) array that is
        approximately twice the size of the available memory:

        >>> size = int(2 * cf.free_memory() / 8)
        >>> d = cf.Data.empty((size,), dtype=float)
        >>> d.fits_in_memory()
        False
        >>> d.nbytes * (1 + 1/8) > cf.free_memory()
        True

        """
        return self.size * (self.dtype.itemsize + 1) <= free_memory()

    @_deprecated_kwarg_check("i")
    @_inplace_enabled(default=False)
    @_manage_log_level_via_verbosity
    @daskified(_DASKIFIED_VERBOSE)
    def where(
        self, condition, x=None, y=None, inplace=False, i=False, verbose=None
    ):
        """Assign array elements depending on a condition.

        The elements to be changed are identified by a
        condition. Different values can be assigned according to where
        the condition is True (assignment from the *x* parameter) or
        False (assignment from the *y* parameter).

        **Missing data**

        Array elements may be set to missing values if either *x* or
        *y* are the `cf.masked` constant, or by assignment from any
        missing data elements in *x* or *y*.

        If the data mask is hard (see the `hardmask` attribute) then
        missing data values in the array will not be overwritten,
        regardless of the content of *x* and *y*.

        If the *condition* contains missing data then the
        corresponding elements in the array will not be assigned to,
        regardless of the contents of *x* and *y*.

        **Broadcasting**

        The array and the *condition*, *x* and *y* parameters must all
        be broadcastable to each other, such that the shape of the
        result is identical to the orginal shape of the array.

        If *condition* is a `Query` object then for the purposes of
        broadcasting, the condition is considered to be that which is
        produced by applying the query to the array.

        **Performance**

        If any of the shapes of the *condition*, *x*, or *y*
        parameters, or the array, is unknown, then there is a
        possibility that an unknown shape will need to be calculated
        immediately by executing all delayed operations on that
        object.

        .. seealso:: `cf.masked`, `hardmask`, `__setitem__`

        :Parameters:

            condition: array-like or `Query`
                The condition which determines how to assign values to
                the data.

                Assignment from the *x* and *y* parameters will be
                done where elements of the condition evaluate to
                `True` and `False` respectively.

                If *condition* is a `Query` object then this implies a
                condition defined by applying the query to the data.

                *Parameter example:*
                  ``d.where(d < 0, x=-999)`` will set all data
                  values that are less than zero to -999.

                *Parameter example:*
                  ``d.where(True, x=-999)`` will set all data values
                  to -999. This is equivalent to ``d[...] = -999``.

                *Parameter example:*
                  ``d.where(False, y=-999)`` will set all data values
                  to -999. This is equivalent to ``d[...] = -999``.

                *Parameter example:*
                  If ``d`` has shape ``(5, 3)`` then ``d.where([True,
                  False, True], x=-999, y=cf.masked)`` will set data
                  values in columns 0 and 2 to -999, and data values
                  in column 1 to missing data. This works because the
                  condition has shape ``(3,)`` which broadcasts to the
                  data shape.

                *Parameter example:*
                  ``d.where(cf.lt(0), x=-999)`` will set all data
                  values that are less than zero to -999. This is
                  equivalent to ``d.where(d < 0, x=-999)``.

            x, y: array-like or `None`
                Specify the assignment values. Where the condition is
                True assign to the data from *x*, and where the
                condition is False assign to the data from *y*.

                If *x* is `None` (the default) then no assignment is
                carried out where the condition is True.

                If *y* is `None` (the default) then no assignment is
                carried out where the condition is False.

                *Parameter example:*
                  ``d.where(condition)``, for any ``condition``, returns
                  data with identical data values.

                *Parameter example:*
                  ``d.where(cf.lt(0), x=-d, y=cf.masked)`` will change the
                  sign of all negative data values, and set all other data
                  values to missing data.

                *Parameter example:*
                  ``d.where(cf.lt(0), x=-d)`` will change the sign of
                  all negative data values, and leave all other data
                  values unchanged. This is equivalent to, but faster
                  than, ``d.where(cf.lt(0), x=-d, y=d)``

            {{inplace: `bool`, optional}}

            {{verbose: `int` or `str` or `None`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`
                The new data with updated values, or `None` if the
                operation was in-place.

        **Examples**

        >>> d = cf.Data([0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
        >>> e = d.where(d < 5, d, 10 * d)
        >>> print(e.array)
        [ 0  1  2  3  4 50 60 70 80 90]

        >>> d = cf.Data([0, 1, 2, 3, 4, 5, 6, 7, 8, 9], 'km')
        >>> e = d.where(d < 5, cf.Data(10000 * d, 'metre'))
        >>> print(e.array)
        [ 0. 10. 20. 30. 40.  5.  6.  7.  8.  9.]

        >>> e = d.where(d < 5, cf.masked)
        >>> print(e.array)
        [-- -- -- -- -- 5 6 7 8 9]

        >>> d = cf.Data([[1, 2,],
        ...              [3, 4]])
        >>> e = d.where([[True, False], [True, True]], d, [[9, 8], [7, 6]])
        >>> print(e.array)
        [[1 8]
         [3 4]]
        >>> e = d.where([[True, False], [True, True]], [[9, 8], [7, 6]])
        >>> print(e.array)
        [[9 2]
         [7 6]]

        The shape of the result must have the same shape as the
        original data:

        >>> e = d.where([True, False], [9, 8])
        >>> print(e.array)
        [[9 2]
         [9 4]]

        >>> d = cf.Data(np.array([[0, 1, 2],
        ...                       [0, 2, 4],
        ...                       [0, 3, 6]]))
        >>> d.where(d < 4, None, -1)
        >>> print(e.array)
        [[ 0  1  2]
         [ 0  2 -1]
         [ 0  3 -1]]

        >>> x, y = np.ogrid[:3, :4]
        >>> print(x)
        [[0]
         [1]
         [2]]
        >>> print(y)
        [[0 1 2 3]]
        >>> condition = x < y
        >>> print(condition)
        [[False  True  True  True]
         [False False  True  True]
         [False False False  True]]
        >>> d = cf.Data(x)
        >>> e = d.where(condition, d, 10 + y)
            ...
        ValueError: where: Broadcasting the 'condition' parameter with shape (3, 4) would change the shape of the data with shape (3, 1)

        >>> d = cf.Data(np.arange(9).reshape(3, 3))
        >>> e = d.copy()
        >>> e[1, 0] = cf.masked
        >>> f = e.where(d > 5, None, -3.1416)
        >>> print(f.array)
        [[-3.1416 -3.1416 -3.1416]
         [-- -3.1416 -3.1416]
         [6.0 7.0 8.0]]
        >>> e.soften_mask()
        >>> f = e.where(d > 5, None, -3.1416)
        >>> print(f.array)
        [[-3.1416 -3.1416 -3.1416]
         [-3.1416 -3.1416 -3.1416]
         [ 6.      7.      8.    ]]

        """
        d = _inplace_enabled_define_and_cleanup(self)

        # Missing values could be affected, so make sure that the mask
        # hardness has been applied.
        dx = d.to_dask_array(apply_mask_hardness=True)

        units = d.Units

        # Parse condition
        if getattr(condition, "isquery", False):
            # Condition is a cf.Query object: Make sure that the
            # condition units are OK, and convert the condition to a
            # boolean dask array with the same shape as the data.
            condition = condition.copy()
            condition = condition.set_condition_units(units)
            condition = condition.evaluate(d)

        condition = type(self).asdata(condition)
        _where_broadcastable(d, condition, "condition")

        # If x or y is self then change it to None. This prevents an
        # unnecessary copy; and, at compute time, an unncessary numpy
        # where.
        if x is self:
            x = None

        if y is self:
            y = None

        if x is None and y is None:
            # The data is unchanged regardless of the condition
            return d

        # Parse x and y
        xy = []
        for arg, name in zip((x, y), ("x", "y")):
            if arg is None:
                xy.append(arg)
                continue

            if arg is cf_masked:
                # Replace masked constant with array
                xy.append(scalar_masked_array(self.dtype))
                continue

            arg = type(self).asdata(arg)
            _where_broadcastable(d, arg, name)

            if arg.Units:
                # Make sure that units are OK.
                arg = arg.copy()
                try:
                    arg.Units = units
                except ValueError:
                    raise ValueError(
                        f"where: {name!r} parameter units {arg.Units!r} "
                        f"are not equivalent to data units {units!r}"
                    )

            xy.append(arg.to_dask_array())

        x, y = xy

        # Apply the where operation
        dx = da.core.elemwise(
            cf_where, dx, da.asanyarray(condition), x, y, d.hardmask
        )
        d._set_dask(dx)

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_deprecated_kwarg_check("i")
    @_inplace_enabled(default=False)
    def sin(self, inplace=False, i=False):
        """Take the trigonometric sine of the data element-wise.

        Units are accounted for in the calculation. If the units are not
        equivalent to radians (such as Kelvin) then they are treated as if
        they were radians. For example, the sine of 90 degrees_east
        is 1.0, as is the sine of 1.57079632 radians.

        The output units are changed to '1' (nondimensional).

        .. seealso:: `arcsin`, `cos`, `tan`, `sinh`

        :Parameters:

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`

        **Examples**

        >>> d.Units
        <Units: degrees_north>
        >>> print(d.array)
        [[-90 0 90 --]]
        >>> e = d.sin()
        >>> e.Units
        <Units: 1>
        >>> print(e.array)
        [[-1.0 0.0 1.0 --]]

        >>> d.Units
        <Units: m s-1>
        >>> print(d.array)
        [[1 2 3 --]]
        >>> d.sin(inplace=True)
        >>> d.Units
        <Units: 1>
        >>> print(d.array)
        [[0.841470984808 0.909297426826 0.14112000806 --]]

        """
        d = _inplace_enabled_define_and_cleanup(self)

        if d.Units.equivalent(_units_radians):
            d.Units = _units_radians

        dx = d.to_dask_array()
        d._set_dask(da.sin(dx))

        d.override_units(_units_1, inplace=True)

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_deprecated_kwarg_check("i")
    @_inplace_enabled(default=False)
    def sinh(self, inplace=False):
        """Take the hyperbolic sine of the data element-wise.

        Units are accounted for in the calculation. If the units are not
        equivalent to radians (such as Kelvin) then they are treated as if
        they were radians. For example, the the hyperbolic sine of 90
        degrees_north is 2.30129890, as is the hyperbolic sine of
        1.57079632 radians.

        The output units are changed to '1' (nondimensional).

        .. versionadded:: 3.1.0

        .. seealso:: `arcsinh`, `cosh`, `tanh`, `sin`

        :Parameters:

            {{inplace: `bool`, optional}}

        :Returns:

            `Data` or `None`

        **Examples**

        >>> d.Units
        <Units: degrees_north>
        >>> print(d.array)
        [[-90 0 90 --]]
        >>> e = d.sinh()
        >>> e.Units
        <Units: 1>
        >>> print(e.array)
        [[-2.3012989023072947 0.0 2.3012989023072947 --]]

        >>> d.Units
        <Units: m s-1>
        >>> print(d.array)
        [[1 2 3 --]]
        >>> d.sinh(inplace=True)
        >>> d.Units
        <Units: 1>
        >>> print(d.array)
        [[1.1752011936438014 3.626860407847019 10.017874927409903 --]]

        """
        d = _inplace_enabled_define_and_cleanup(self)

        if d.Units.equivalent(_units_radians):
            d.Units = _units_radians

        dx = d.to_dask_array()
        d._set_dask(da.sinh(dx))

        d.override_units(_units_1, inplace=True)

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def cosh(self, inplace=False):
        """Take the hyperbolic cosine of the data element-wise.

        Units are accounted for in the calculation. If the units are not
        equivalent to radians (such as Kelvin) then they are treated as if
        they were radians. For example, the the hyperbolic cosine of 0
        degrees_east is 1.0, as is the hyperbolic cosine of 1.57079632 radians.

        The output units are changed to '1' (nondimensional).

        .. versionadded:: 3.1.0

        .. seealso:: `arccosh`, `sinh`, `tanh`, `cos`

        :Parameters:

            {{inplace: `bool`, optional}}

        :Returns:

            `Data` or `None`

        **Examples**

        >>> d.Units
        <Units: degrees_north>
        >>> print(d.array)
        [[-90 0 90 --]]
        >>> e = d.cosh()
        >>> e.Units
        <Units: 1>
        >>> print(e.array)
        [[2.5091784786580567 1.0 2.5091784786580567 --]]

        >>> d.Units
        <Units: m s-1>
        >>> print(d.array)
        [[1 2 3 --]]
        >>> d.cosh(inplace=True)
        >>> d.Units
        <Units: 1>
        >>> print(d.array)
        [[1.5430806348152437 3.7621956910836314 10.067661995777765 --]]

        """
        d = _inplace_enabled_define_and_cleanup(self)

        if d.Units.equivalent(_units_radians):
            d.Units = _units_radians

        dx = d.to_dask_array()
        d._set_dask(da.cosh(dx))

        d.override_units(_units_1, inplace=True)

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_deprecated_kwarg_check("i")
    @_inplace_enabled(default=False)
    def tanh(self, inplace=False):
        """Take the hyperbolic tangent of the data element-wise.

        Units are accounted for in the calculation. If the units are not
        equivalent to radians (such as Kelvin) then they are treated as if
        they were radians. For example, the the hyperbolic tangent of 90
        degrees_east is 0.91715234, as is the hyperbolic tangent of
        1.57079632 radians.

        The output units are changed to '1' (nondimensional).

        .. versionadded:: 3.1.0

        .. seealso:: `arctanh`, `sinh`, `cosh`, `tan`


        :Parameters:

            {{inplace: `bool`, optional}}

        :Returns:

            `Data` or `None`

        **Examples**

        >>> d.Units
        <Units: degrees_north>
        >>> print(d.array)
        [[-90 0 90 --]]
        >>> e = d.tanh()
        >>> e.Units
        <Units: 1>
        >>> print(e.array)
        [[-0.9171523356672744 0.0 0.9171523356672744 --]]

        >>> d.Units
        <Units: m s-1>
        >>> print(d.array)
        [[1 2 3 --]]
        >>> d.tanh(inplace=True)
        >>> d.Units
        <Units: 1>
        >>> print(d.array)
        [[0.7615941559557649 0.9640275800758169 0.9950547536867305 --]]

        """
        d = _inplace_enabled_define_and_cleanup(self)

        if d.Units.equivalent(_units_radians):
            d.Units = _units_radians

        dx = d.to_dask_array()
        d._set_dask(da.tanh(dx))

        d.override_units(_units_1, inplace=True)

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_deprecated_kwarg_check("i")
    @_inplace_enabled(default=False)
    def log(self, base=None, inplace=False, i=False):
        """Takes the logarithm of the data array.

        :Parameters:

            base:

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`

        """
        d = _inplace_enabled_define_and_cleanup(self)
        dx = d.to_dask_array()

        if base is None:
            dx = da.log(dx)
        elif base == 10:
            dx = da.log10(dx)
        elif base == 2:
            dx = da.log2(dx)
        else:
            dx = da.log(dx)
            dx /= da.log(base)

        d._set_dask(dx)

        d.override_units(
            _units_1, inplace=True
        )  # all logarithm outputs are unitless

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_deprecated_kwarg_check("i")
    @_inplace_enabled(default=False)
    def squeeze(self, axes=None, inplace=False, i=False):
        """Remove size 1 axes from the data array.

        By default all size 1 axes are removed, but particular axes
        may be selected with the keyword arguments.

        .. seealso:: `flatten`, `insert_dimension`, `flip`,
                     `swapaxes`, `transpose`

        :Parameters:

            axes: (sequence of) int, optional
                Select the axes. By default all size 1 axes are
                removed. The *axes* argument may be one, or a
                sequence, of integers that select the axis
                corresponding to the given position in the list of
                axes of the data array.

                No axes are removed if *axes* is an empty sequence.

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`
                The squeezed data array.

        **Examples**

        >>> v.shape
        (1,)
        >>> v.squeeze()
        >>> v.shape
        ()

        >>> v.shape
        (1, 2, 1, 3, 1, 4, 1, 5, 1, 6, 1)
        >>> v.squeeze((0,))
        >>> v.shape
        (2, 1, 3, 1, 4, 1, 5, 1, 6, 1)
        >>> v.squeeze(1)
        >>> v.shape
        (2, 3, 1, 4, 1, 5, 1, 6, 1)
        >>> v.squeeze([2, 4])
        >>> v.shape
        (2, 3, 4, 5, 1, 6, 1)
        >>> v.squeeze([])
        >>> v.shape
        (2, 3, 4, 5, 1, 6, 1)
        >>> v.squeeze()
        >>> v.shape
        (2, 3, 4, 5, 6)

        """
        d = _inplace_enabled_define_and_cleanup(self)

        # TODODASK - check if axis parsing is done in dask

        if not d.ndim:
            if axes or axes == 0:
                raise ValueError(
                    "Can't squeeze: Can't remove an axis from "
                    f"scalar {d.__class__.__name__}"
                )

            if inplace:
                d = None

            return d

        shape = d.shape

        if axes is None:
            axes = [i for i, n in enumerate(shape) if n == 1]
        else:
            axes = d._parse_axes(axes)

            # Check the squeeze axes
            for i in axes:
                if shape[i] > 1:
                    raise ValueError(
                        f"Can't squeeze {d.__class__.__name__}: "
                        f"Can't remove axis of size {shape[i]}"
                    )

        if not axes:
            return d

        # Still here? Then the data array is not scalar and at least
        # one size 1 axis needs squeezing.
        dx = d.to_dask_array()
        dx = dx.squeeze(axis=tuple(axes))
        d._set_dask(dx)

        # Remove the squeezed axes names
        d._axes = [axis for i, axis in enumerate(d._axes) if i not in axes]

        return d

    # `arctan2`, AT2 seealso
    @daskified(_DASKIFIED_VERBOSE)
    @_deprecated_kwarg_check("i")
    @_inplace_enabled(default=False)
    def tan(self, inplace=False, i=False):
        """Take the trigonometric tangent of the data element-wise.

        Units are accounted for in the calculation. If the units are not
        equivalent to radians (such as Kelvin) then they are treated as if
        they were radians. For example, the tangents of 45
        degrees_east, 0.78539816 radians and 0.78539816 Kelvin are all
        1.0.

        The output units are changed to '1' (nondimensional).

        .. seealso:: `arctan`, `cos`, `sin`, `tanh`

        :Parameters:

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`

        **Examples**

        >>> d.Units
        <Units: degrees_north>
        >>> print(d.array)
        [[-45 0 45 --]]
        >>> e = d.tan()
        >>> e.Units
        <Units: 1>
        >>> print(e.array)
        [[-1.0 0.0 1.0 --]]

        >>> d.Units
        <Units: m s-1>
        >>> print(d.array)
        [[1 2 3 --]]
        >>> d.tan(inplace=True)
        >>> d.Units
        <Units: 1>
        >>> print(d.array)
        [[1.55740772465 -2.18503986326 -0.142546543074 --]]

        """
        d = _inplace_enabled_define_and_cleanup(self)

        if d.Units.equivalent(_units_radians):
            d.Units = _units_radians

        dx = d.to_dask_array()
        d._set_dask(da.tan(dx))

        d.override_units(_units_1, inplace=True)

        return d

    @daskified(_DASKIFIED_VERBOSE)
    def tolist(self):
        """Return the data as a scalar or (nested) list.

        Returns the data as an ``N``-levels deep nested list of Python
        scalars, where ``N`` is the number of data dimensions.

        If ``N`` is 0 then, since the depth of the nested list is 0,
        it will not be a list at all, but a simple Python scalar.

        :Returns:

            `list` or scalar
                The (nested) list of array elements, or a scalar if
                the data has 0 dimensions.

        **Examples**

        >>> d = cf.Data(9)
        >>> d.tolist()
        9

        >>> d = cf.Data([1, 2])
        >>> d.tolist()
        [1, 2]

        >>> d = cf.Data(([[1, 2], [3, 4]]))
        >>> d.tolist()
        [[1, 2], [3, 4]]

        >>> d.equals(cf.Data(d.tolist()))
        True

        """
        return self.array.tolist()

    @daskified(_DASKIFIED_VERBOSE)
    def to_memory(self):
        """Bring data on disk into memory.

        Not implemented. Consider using `persist` instead.

        """
        raise NotImplementedError(
            "'Data.to_memory' is not available. "
            "Consider using 'Data.persist' instead."
        )

    @daskified(_DASKIFIED_VERBOSE)
    @_deprecated_kwarg_check("i")
    @_inplace_enabled(default=False)
    def transpose(self, axes=None, inplace=False, i=False):
        """Permute the axes of the data array.

        .. seealso:: `flatten', `insert_dimension`, `flip`, `squeeze`,
                     `swapaxes`

        :Parameters:

            axes: (sequence of) `int`
                The new axis order of the data array. By default the order
                is reversed. Each axis of the new order is identified by
                its original integer position.

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`

        **Examples**

        >>> d.shape
        (19, 73, 96)
        >>> d.transpose()
        >>> d.shape
        (96, 73, 19)
        >>> d.transpose([1, 0, 2])
        >>> d.shape
        (73, 96, 19)
        >>> d.transpose((-1, 0, 1))
        >>> d.shape
        (19, 73, 96)

        """
        d = _inplace_enabled_define_and_cleanup(self)

        ndim = d.ndim
        if axes is None:
            if ndim <= 1:
                return d
            iaxes = tuple(range(ndim - 1, -1, -1))
        else:
            iaxes = d._parse_axes(axes)

        # Note: _axes attribute is still important/utilised post-Daskification
        # because e.g. axes labelled as cyclic by the _cyclic attribute use it
        # to determine their position (see #discussion_r694096462 on PR #247).
        data_axes = d._axes
        d._axes = [data_axes[i] for i in iaxes]

        dx = d.to_dask_array()
        try:
            dx = da.transpose(dx, axes=axes)
        except ValueError:
            raise ValueError(
                f"Can't transpose: Axes don't match array: {axes}"
            )
        d._set_dask(dx)

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_deprecated_kwarg_check("i")
    @_inplace_enabled(default=False)
    def trunc(self, inplace=False, i=False):
        """Return the truncated values of the data array.

        The truncated value of the number, ``x``, is the nearest integer
        which is closer to zero than ``x`` is. In short, the fractional
        part of the signed number ``x`` is discarded.

        .. versionadded:: 1.0

        .. seealso:: `ceil`, `floor`, `rint`

        :Parameters:

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`

        **Examples**

        >>> d = cf.Data([-1.9, -1.5, -1.1, -1, 0, 1, 1.1, 1.5 , 1.9])
        >>> print(d.array)
        [-1.9 -1.5 -1.1 -1.   0.   1.   1.1  1.5  1.9]
        >>> print(d.trunc().array)
        [-1. -1. -1. -1.  0.  1.  1.  1.  1.]

        """
        d = _inplace_enabled_define_and_cleanup(self)
        dx = d.to_dask_array()
        d._set_dask(da.trunc(dx))
        return d

    @classmethod
    def empty(
        cls,
        shape,
        dtype=None,
        units=None,
        calendar=None,
        fill_value=None,
        chunks=_DEFAULT_CHUNKS,
    ):
        """Return a new array of given shape and type, without
        initializing entries.

        .. seealso:: `full`, `ones`, `zeros`

        :Parameters:

            shape: `int` or `tuple` of `int`
                The shape of the new array. e.g. ``(2, 3)`` or ``2``.

            dtype: data-type
                The desired output data-type for the array, e.g.
                `numpy.int8`. The default is `numpy.float64`.

            units: `str` or `Units`
                The units for the new data array.

            calendar: `str`, optional
                The calendar for reference time units.

            {{chunks: `int`, `tuple`, `dict` or `str`, optional}}

                .. versionadded:: 4.0.0

            fill_value: deprecated at version 4.0.0
                Use `set_fill_value` instead.

        :Returns:

            `Data`
                Array of uninitialized (arbitrary) data of the given
                shape and dtype.

        **Examples**

        >>> d = cf.Data.empty((2, 2))
        >>> print(d.array)
        [[ -9.74499359e+001  6.69583040e-309],
         [  2.13182611e-314  3.06959433e-309]]         #uninitialized

        >>> d = cf.Data.empty((2,), dtype=bool)
        >>> print(d.array)
        [ False  True]                                 #uninitialized

        """
        dx = da.empty(shape, dtype=dtype, chunks=chunks)
        return cls(dx, units=units, calendar=calendar)

    @classmethod
    def full(
        cls,
        shape,
        fill_value,
        dtype=None,
        units=None,
        calendar=None,
        chunks=_DEFAULT_CHUNKS,
    ):
        """Return a new array of given shape and type, filled with a
        fill value.

        .. seealso:: `empty`, `ones`, `zeros`

        :Parameters:

            shape: `int` or `tuple` of `int`
                The shape of the new array. e.g. ``(2, 3)`` or ``2``.

            fill_value: scalar
                The fill value.

            dtype: data-type
                The desired data-type for the array. The default, `None`,
                means ``np.array(fill_value).dtype``.

            units: `str` or `Units`
                The units for the new data array.

            calendar: `str`, optional
                The calendar for reference time units.

            {{chunks: `int`, `tuple`, `dict` or `str`, optional}}

                .. versionadded:: 4.0.0

        :Returns:

            `Data`
                Array of *fill_value* with the given shape and data
                type.

        **Examples**

        >>> d = cf.Data.full((2, 3), -99)
        >>> print(d.array)
        [[-99 -99 -99]
         [-99 -99 -99]]

        >>> d = cf.Data.full(2, 0.0)
        >>> print(d.array)
        [0. 0.]

        >>> d = cf.Data.full((2,), 0, dtype=bool)
        >>> print(d.array)
        [False False]

        """
        if dtype is None:
            # Need to explicitly set the default because dtype is not
            # a named keyword of da.full
            dtype = getattr(fill_value, "dtype", None)
            if dtype is None:
                dtype = np.array(fill_value).dtype

        dx = da.full(shape, fill_value, dtype=dtype, chunks=chunks)
        return cls(dx, units=units, calendar=calendar)

    @classmethod
    def ones(
        cls,
        shape,
        dtype=None,
        units=None,
        calendar=None,
        chunks=_DEFAULT_CHUNKS,
    ):
        """Returns a new array filled with ones of set shape and type.

        .. seealso:: `empty`, `full`, `zeros`

        :Parameters:

            shape: `int` or `tuple` of `int`
                The shape of the new array. e.g. ``(2, 3)`` or ``2``.

            dtype: data-type
                The desired data-type for the array, e.g.
                `numpy.int8`. The default is `numpy.float64`.

            units: `str` or `Units`
                The units for the new data array.

            calendar: `str`, optional
                The calendar for reference time units.

            {{chunks: `int`, `tuple`, `dict` or `str`, optional}}

                .. versionadded:: 4.0.0

        :Returns:

            `Data`
                Array of ones with the given shape and data type.

        **Examples**

        >>> d = cf.Data.ones((2, 3))
        >>> print(d.array)
        [[1. 1. 1.]
         [1. 1. 1.]]

        >>> d = cf.Data.ones((2,), dtype=bool)
        >>> print(d.array)
        [ True  True]

        """
        dx = da.ones(shape, dtype=dtype, chunks=chunks)
        return cls(dx, units=units, calendar=calendar)

    @classmethod
    def zeros(
        cls,
        shape,
        dtype=None,
        units=None,
        calendar=None,
        chunks=_DEFAULT_CHUNKS,
    ):
        """Returns a new array filled with zeros of set shape and type.

        .. seealso:: `empty`, `full`, `ones`

        :Parameters:

            shape: `int` or `tuple` of `int`
                The shape of the new array.

            dtype: data-type
                The data-type of the new array. By default the
                data-type is ``float``.

            units: `str` or `Units`
                The units for the new data array.

            calendar: `str`, optional
                The calendar for reference time units.

            {{chunks: `int`, `tuple`, `dict` or `str`, optional}}

                .. versionadded:: 4.0.0

        :Returns:

            `Data`
                Array of zeros with the given shape and data type.

        **Examples**

        >>> d = cf.Data.zeros((2, 3))
        >>> print(d.array)
        [[0. 0. 0.]
         [0. 0. 0.]]

        >>> d = cf.Data.zeros((2,), dtype=bool)
        >>> print(d.array)
        [False False]

        """
        dx = da.zeros(shape, dtype=dtype, chunks=chunks)
        return cls(dx, units=units, calendar=calendar)

    @daskified(_DASKIFIED_VERBOSE)
    @_deprecated_kwarg_check("out")
    @_deprecated_kwarg_check("i")
    @_inplace_enabled(default=False)
    def func(
        self,
        f,
        units=None,
        out=False,
        inplace=False,
        preserve_invalid=False,
        i=False,
        **kwargs,
    ):
        """Apply an element-wise array operation to the data array.

        :Parameters:

            f: `function`
                The function to be applied.

            units: `Units`, optional

            out: deprecated at version 4.0.0

            {{inplace: `bool`, optional}}

            preserve_invalid: `bool`, optional
                For MaskedArray arrays only, if True any invalid values produced
                by the operation will be preserved, otherwise they are masked.

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`

        **Examples**

        >>> d.Units
        <Units: radians>
        >>> print(d.array)
        [[ 0.          1.57079633]
         [ 3.14159265  4.71238898]]
        >>> import numpy
        >>> e = d.func(numpy.cos)
        >>> e.Units
        <Units: 1>
        >>> print(e.array)
        [[ 1.0  0.0]
         [-1.0  0.0]]
        >>> d.func(numpy.sin, inplace=True)
        >>> print(d.array)
        [[0.0   1.0]
         [0.0  -1.0]]

        >>> d = cf.Data([-2, -1, 1, 2], mask=[0, 0, 0, 1])
        >>> f = d.func(numpy.arctanh, preserve_invalid=True)
        >>> f.array
        masked_array(data=[nan, -inf, inf, --],
                     mask=[False, False, False,  True],
               fill_value=1e+20)
        >>> e = d.func(numpy.arctanh)  # default preserve_invalid is False
        >>> e.array
        masked_array(data=[--, --, --, --],
                     mask=[ True,  True,  True,  True],
               fill_value=1e+20,
                    dtype=float64)

        """
        d = _inplace_enabled_define_and_cleanup(self)

        dx = d.to_dask_array()

        # TODODASK: Steps to preserve invalid values shown, taking same
        # approach as pre-daskification, but maybe we can now change approach
        # to avoid finding mask and data, which requires early compute...
        # Step 1. extract the non-masked data and the mask separately
        if preserve_invalid:
            # Assume all inputs are masked, as checking for a mask to confirm
            # is expensive. If unmasked, effective mask will be all False.
            dx_mask = da.ma.getmaskarray(dx)  # store original mask
            dx = da.ma.getdata(dx)

        # Step 2: apply operation to data alone
        axes = tuple(range(dx.ndim))
        dx = da.blockwise(f, axes, dx, axes, **kwargs)

        if preserve_invalid:
            # Step 3: reattach original mask onto the output data
            dx = da.ma.masked_array(dx, mask=dx_mask)

        d._set_dask(dx)

        if units is not None:
            d.override_units(units, inplace=True)

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    @_deprecated_kwarg_check("i")
    def range(
        self,
        axes=None,
        squeeze=False,
        mtol=1,
        split_every=None,
        inplace=False,
        i=False,
    ):
        """Calculate range values.

        The range is the maximum minus the minimum.

        Calculates the range value or the range values along axes.

        See
        https://ncas-cms.github.io/cf-python/analysis.html#collapse-methods
        for mathematical definitions.

         ..seealso:: `sample_size`, `max`, `min`, `mid_range`

        :Parameters:

            {{collapse axes: (sequence of) `int`, optional}}

            {{collapse squeeze: `bool`, optional}}

            {{mtol: number, optional}}

            {{split_every: `int` or `dict`, optional}}

                .. versionadded:: TODODASKVER

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`
                The collapsed array.

        **Examples**

        >>> a = np.ma.arange(12).reshape(4, 3)
        >>> d = cf.Data(a, 'K')
        >>> d[1, 1] = cf.masked
        >>> print(d.array)
        [[0 1 2]
         [3 -- 5]
         [6 7 8]
         [9 10 11]]
        >>> d.range()
        <CF Data(1, 1): [[11]] K>

        """
        d = _inplace_enabled_define_and_cleanup(self)
        d, _ = _collapse(
            Collapse.range,
            d,
            axis=axes,
            keepdims=not squeeze,
            split_every=split_every,
            mtol=mtol,
        )
        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    @_deprecated_kwarg_check("i")
    def roll(self, axis, shift, inplace=False, i=False):
        """Roll array elements along a given axis.

        Equivalent in function to `numpy.roll`.

        TODODASKDOCS  - note that it works for multiple axes

        :Parameters:

            axis: `int`
                Select the axis over which the elements are to be rolled.
                removed. The *axis* parameter is an integer that selects
                the axis corresponding to the given position in the list
                of axes of the data.

                *Parameter example:*
                  Convolve the second axis: ``axis=1``.

                *Parameter example:*
                  Convolve the last axis: ``axis=-1``.

            shift: `int`, or `tuple` of `int`
                The number of places by which elements are shifted.
                If a `tuple`, then *axis* must be a tuple of the same
                size, and each of the given axes is shifted by the
                corresponding number. If an `int` while *axis* is a
                tuple of `int`, then the same value is used for all
                given axes.

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`

        """
        # TODODASKAPI - consider matching the numpy/dask api: "shift, axis="

        d = _inplace_enabled_define_and_cleanup(self)

        dx = d.to_dask_array()
        dx = da.roll(dx, shift, axis=axis)
        d._set_dask(dx)

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    @_deprecated_kwarg_check("i")
    def sum(
        self,
        axes=None,
        weights=None,
        squeeze=False,
        mtol=1,
        split_every=None,
        inplace=False,
        i=False,
    ):
        """Calculate sum values.

        Calculates the sum value or the sum values along axes.

        See
        https://ncas-cms.github.io/cf-python/analysis.html#collapse-methods
        for mathematical definitions.

         ..seealso:: `sample_size`, `integral`, `mean`, `sd`,
                     `sum_of_squares`, `sum_of_weights`

        :Parameters:

            {{collapse axes: (sequence of) `int`, optional}}

            {{weights: data_like, `dict`, or `None`, optional}}

            {{collapse squeeze: `bool`, optional}}

            {{mtol: number, optional}}

            {{split_every: `int` or `dict`, optional}}

                .. versionadded:: TODODASKVER

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`
                The collapsed data, or `None` if the operation was
                in-place.

        **Examples**

        >>> a = np.ma.arange(12).reshape(4, 3)
        >>> d = cf.Data(a, 'K')
        >>> d[1, 1] = cf.masked
        >>> print(d.array)
        [[0 1 2]
         [3 -- 5]
         [6 7 8]
         [9 10 11]]
        >>> d.sum()
        <CF Data(1, 1): [[62]] K>

        >>> w = np.linspace(1, 2, 3)
        >>> print(w)
        [1.  1.5 2. ]
        >>> d.sum(weights=cf.Data(w, 'm'))
        <CF Data(1, 1): [[97.0]] K>

        """
        d = _inplace_enabled_define_and_cleanup(self)
        d, _ = _collapse(
            Collapse.sum,
            d,
            axis=axes,
            weights=weights,
            keepdims=not squeeze,
            split_every=split_every,
            mtol=mtol,
        )
        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def sum_of_squares(
        self,
        axes=None,
        weights=None,
        squeeze=False,
        mtol=1,
        split_every=None,
        inplace=False,
    ):
        """Calculate sums of squares.

        Calculates the sum of squares or the sum of squares values
        along axes.

        See
        https://ncas-cms.github.io/cf-python/analysis.html#collapse-methods
        for mathematical definitions.

         ..seealso:: `sample_size`, `sum`, `sum_of_squares`,
                     `sum_of_weights2`

        :Parameters:

            {{collapse axes: (sequence of) `int`, optional}}

            {{weights: data_like, `dict`, or `None`, optional}}

            {{collapse squeeze: `bool`, optional}}

            {{mtol: number, optional}}

            {{split_every: `int` or `dict`, optional}}

                .. versionadded:: TODODASKVER

            {{inplace: `bool`, optional}}

        :Returns:

            `Data` or `None`
                The collapsed data, or `None` if the operation was
                in-place.

        **Examples**

        >>> a = np.ma.arange(12).reshape(4, 3)
        >>> d = cf.Data(a, 'K')
        >>> d[1, 1] = cf.masked
        >>> print(d.array)
        [[0 1 2]
         [3 -- 5]
         [6 7 8]
         [9 10 11]]
        >>> d.sum_of_squares()
        <CF Data(1, 1): [[490]] K2>

        >>> w = np.linspace(1, 2, 3)
        >>> print(w)
        [1.  1.5 2. ]
        >>> d.sum_of_squares(weights=w)
        <CF Data(1, 1): [[779.0]] K2>

        """
        d = _inplace_enabled_define_and_cleanup(self)
        d.square(inplace=True)
        d.sum(
            axes=axes,
            weights=weights,
            squeeze=squeeze,
            mtol=mtol,
            split_every=split_every,
            inplace=True,
        )
        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_deprecated_kwarg_check("i")
    @_inplace_enabled(default=False)
    def sum_of_weights(
        self,
        axes=None,
        weights=None,
        squeeze=False,
        mtol=1,
        split_every=None,
        inplace=False,
        i=False,
    ):
        """Calculate sums of weights.

        Calculates the sum of weights or the sum of weights values
        along axes.

        The weights given by the *weights* parameter are internally
        broadcast to the shape of the data, and those weights that are
        missing data, or that correspond to the missing elements of
        the data, are assigned a weight of 0. It is these processed
        weights that are summed.

        See
        https://ncas-cms.github.io/cf-python/analysis.html#collapse-methods
        for mathematical definitions.

         ..seealso:: `sample_size`, `sum`, `sum_of_squares`,
                     `sum_of_weights2`

        :Parameters:

            {{collapse axes: (sequence of) `int`, optional}}

            {{weights: data_like, `dict`, or `None`, optional}}

            {{collapse squeeze: `bool`, optional}}

            {{mtol: number, optional}}

            {{split_every: `int` or `dict`, optional}}

                .. versionadded:: TODODASKVER

            {[inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`
                The collapsed data, or `None` if the operation was
                in-place.

        **Examples**

        >>> a = np.ma.arange(12).reshape(4, 3)
        >>> d = cf.Data(a, 'K')
        >>> d[1, 1] = cf.masked
        >>> print(d.array)
        [[0 1 2]
         [3 -- 5]
         [6 7 8]
         [9 10 11]]
        >>> d.sum_of_weights()
        <CF Data(1, 1): [[11]]>

        >>> w = np.linspace(1, 2, 3)
        >>> print(w)
        [1.  1.5 2. ]
        >>> d.sum_of_weights(weights=w)
        <CF Data(1, 1): [[16.5]]>

        >>> d.sum_of_weights(weights=cf.Data(w, 'm'))
        <CF Data(1, 1): [[16.5]] m>

        """
        d = _inplace_enabled_define_and_cleanup(self)
        d, weights = _collapse(
            Collapse.sum_of_weights,
            d,
            axis=axes,
            weights=weights,
            keepdims=not squeeze,
            split_every=split_every,
            mtol=mtol,
        )

        units = _units_None
        if weights is not None:
            units = getattr(weights, "Units", None)
            if units is None:
                units = _units_None

        d.override_units(units, inplace=True)

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_deprecated_kwarg_check("i")
    @_inplace_enabled(default=False)
    def sum_of_weights2(
        self,
        axes=None,
        weights=None,
        squeeze=False,
        mtol=1,
        split_every=None,
        inplace=False,
        i=False,
    ):
        """Calculate sums of squares of weights.

        Calculates the sum of squares of weights or the sum of squares
        of weights values along axes.

        The weights given by the *weights* parameter are internally
        broadcast to the shape of the data, and those weights that
        are missing data, or that correspond to the missing elements
        of the data, are assigned a weight of 0. It is these processed
        weights that are squared and summed.

        See
        https://ncas-cms.github.io/cf-python/analysis.html#collapse-methods
        for mathematical definitions.

         ..seealso:: `sample_size`, `sum`, `sum_of_squares`,
                     `sum_of_weights`

        :Parameters:

            {{collapse axes: (sequence of) `int`, optional}}

            {{weights: data_like, `dict`, or `None`, optional}}

            {{collapse squeeze: `bool`, optional}}

            {{mtol: number, optional}}

            {{split_every: `int` or `dict`, optional}}

                .. versionadded:: TODODASKVER

            {[inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`
                The collapsed data, or `None` if the operation was
                in-place.

        **Examples**

        >>> a = np.ma.arange(12).reshape(4, 3)
        >>> d = cf.Data(a, 'K')
        >>> d[1, 1] = cf.masked
        >>> print(d.array)
        [[0 1 2]
         [3 -- 5]
         [6 7 8]
         [9 10 11]]
        >>> d.sum_of_weights2()
        <CF Data(1, 1): [[11]]>

        >>> w = np.linspace(1, 2, 3)
        >>> print(w)
        [1.  1.5 2. ]
        >>> d.sum_of_weights2(weights=w)
        <CF Data(1, 1): [[26.75]]>

        >>> d.sum_of_weights2(weights=cf.Data(w, 'm'))
        <CF Data(1, 1): [[26.75]] m2>

        """
        d = _inplace_enabled_define_and_cleanup(self)
        d, weights = _collapse(
            Collapse.sum_of_weights2,
            d,
            axis=axes,
            weights=weights,
            keepdims=not squeeze,
            split_every=split_every,
            mtol=mtol,
        )

        units = _units_None
        if weights is not None:
            units = getattr(weights, "Units", None)
            if not units:
                units = _units_None
            else:
                units = units**2

        d.override_units(units, inplace=True)

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_deprecated_kwarg_check("i")
    @_inplace_enabled(default=False)
    def std(
        self,
        axes=None,
        squeeze=False,
        mtol=1,
        weights=None,
        ddof=0,
        split_every=None,
        inplace=False,
        i=False,
    ):
        r"""Calculate standard deviations.

        Calculates the standard deviation of an array or the standard
        deviations along axes.

        See
        https://ncas-cms.github.io/cf-python/analysis.html#collapse-methods
        for mathematical definitions.

         ..seealso:: `sample_size`, `mean`, `sum`, `var`

        :Parameters:

            {{collapse axes: (sequence of) `int`, optional}}

            {{weights: data_like, `dict`, or `None`, optional}}

            {{collapse squeeze: `bool`, optional}}

            {{mtol: number, optional}}

            {{ddof: number}}

                 By default *ddof* is 0.

            {{split_every: `int` or `dict`, optional}}

                .. versionadded:: TODODASKVER

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`
                The collapsed data, or `None` if the operation was
                in-place.

        **Examples**

        >>> a = np.ma.arange(12).reshape(4, 3)
        >>> d = cf.Data(a, 'K')
        >>> d[1, 1] = cf.masked
        >>> print(d.array)
        [[0 1 2]
         [3 -- 5]
         [6 7 8]
         [9 10 11]]
        >>> d.std()
        <CF Data(1, 1): [[3.5744733184250004]] K>
        >>> d.std(ddof=1)
        <CF Data(1, 1): [[3.7489392439122637]] K>

        >>> w = np.linspace(1, 2, 3)
        >>> print(w)
        [1.  1.5 2. ]
        >>> d.std(ddof=1, weights=w)
        <CF Data(1, 1): [[3.7457375639741506]] K>

        """
        d = _inplace_enabled_define_and_cleanup(self)
        d.var(
            axes=axes,
            weights=weights,
            squeeze=squeeze,
            mtol=mtol,
            ddof=ddof,
            split_every=split_every,
            inplace=True,
        )
        d.sqrt(inplace=True)
        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    @_deprecated_kwarg_check("i")
    def var(
        self,
        axes=None,
        weights=None,
        squeeze=False,
        mtol=1,
        ddof=0,
        split_every=None,
        inplace=False,
        i=False,
    ):
        """Calculate variances.

        Calculates the variance of an array or the variance values
        along axes.

        See
        https://ncas-cms.github.io/cf-python/analysis.html#collapse-methods
        for mathematical definitions.

         ..seealso:: `sample_size`, `mean`, `sd`, `sum`

        :Parameters:

            {{collapse axes: (sequence of) `int`, optional}}

            {{weights: data_like, `dict`, or `None`, optional}}

            {{collapse squeeze: `bool`, optional}}

            {{mtol: number, optional}}

            {{ddof: number}}

                 By default *ddof* is 0.

            {{split_every: `int` or `dict`, optional}}

                .. versionadded:: TODODASKVER

            {{inplace: `bool`, optional}}

            {{i: deprecated at version 3.0.0}}

        :Returns:

            `Data` or `None`
                The collapsed data, or `None` if the operation was
                in-place.

        **Examples**

        >>> a = np.ma.arange(12).reshape(4, 3)
        >>> d = cf.Data(a, 'K')
        >>> d[1, 1] = cf.masked
        >>> print(d.array)
        [[0 1 2]
         [3 -- 5]
         [6 7 8]
         [9 10 11]]
        >>> d.var()
        <CF Data(1, 1): [[12.776859504132233]] K2>
        >>> d.var(ddof=1)
        <CF Data(1, 1): [[14.054545454545456]] K2>

        >>> w = np.linspace(1, 2, 3)
        >>> print(w)
        [1.  1.5 2. ]
        >>> d.var(ddof=1, weights=w)
        <CF Data(1, 1): [[14.030549898167004]] K2>

        """
        d = _inplace_enabled_define_and_cleanup(self)
        d, _ = _collapse(
            Collapse.var,
            d,
            axis=axes,
            weights=weights,
            keepdims=not squeeze,
            mtol=mtol,
            ddof=ddof,
            split_every=split_every,
        )

        units = d.Units
        if units:
            d.override_units(units**2, inplace=True)

        return d

    @daskified(_DASKIFIED_VERBOSE)
    def section(
        self, axes, stop=None, chunks=False, min_step=1, mode="dictionary"
    ):
        """Returns a dictionary of sections of the `Data` object.

        Specifically, returns a dictionary of Data objects which are the
        m-dimensional sections of this n-dimensional Data object, where
        m <= n. The dictionary keys are the indices of the sections
        in the original Data object. The m dimensions that are not
        sliced are marked with None as a placeholder making it possible
        to reconstruct the original data object. The corresponding
        values are the resulting sections of type `Data`.

        :Parameters:

            axes: (sequence of) `int`
                This is should be one or more integers of the m indices of
                the m axes that define the sections of the `Data`
                object. If axes is `None` (the default) or an empty
                sequence then all axes are selected.

                Note that the axes specified by the *axes* parameter are
                the one which are to be kept whole. All other axes are
                sectioned.

            stop: `int`, optional
                Deprecated at version TODODASKVER.

                Stop after this number of sections and return. If stop is
                None all sections are taken.

            chunks: `bool`, optional
                Deprecated at version TODODASKVER. Consider using
                `cf.Data.rechunk` instead.

                If True return sections that are of the maximum possible
                size that will fit in one chunk of memory instead of
                sectioning into slices of size 1 along the dimensions that
                are being sectioned.


            min_step: `int`, optional
                The minimum step size when making chunks. By default this
                is 1. Can be set higher to avoid size 1 dimensions, which
                are problematic for linear regridding.

        :Returns:

            `dict`
                The dictionary of m dimensional sections of the Data
                object.

        **Examples**

        >>> d = cf.Data(np.arange(120).reshape(2, 6, 10))
        >>> d
        <CF Data(2, 6, 10): [[[0, ..., 119]]]>
        >>> d.section([1, 2])
        {(0, None, None): <CF Data(1, 6, 10): [[[0, ..., 59]]]>,
         (1, None, None): <CF Data(1, 6, 10): [[[60, ..., 119]]]>}
        >>> d.section([0, 1], min_step=2)
        {(None, None, 0): <CF Data(2, 6, 2): [[[0, ..., 111]]]>,
         (None, None, 2): <CF Data(2, 6, 2): [[[2, ..., 113]]]>,
         (None, None, 4): <CF Data(2, 6, 2): [[[4, ..., 115]]]>,
         (None, None, 6): <CF Data(2, 6, 2): [[[6, ..., 117]]]>,
         (None, None, 8): <CF Data(2, 6, 2): [[[8, ..., 119]]]>}

        """
        if chunks:
            _DEPRECATION_ERROR_KWARGS(
                self,
                "section",
                {"chunks": chunks},
                message="Consider using Data.rechunk() instead.",
                version="TODODASKVER",
                removed_at="5.0.0",
            )  # pragma: no cover

        if stop is not None:
            _DEPRECATION_ERROR_KWARGS(
                self,
                "section",
                {"stop": stop},
                version="TODODASKVER",
                removed_at="5.0.0",
            )  # pragma: no cover

        return _section(self, axes, min_step=min_step)

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def square(self, dtype=None, inplace=False):
        """Calculate the element-wise square.

        .. versionadded:: TODODASKVER

        .. seealso:: `sqrt`, `sum_of_squares`

        :Parameters:

            dtype: data-type, optional
                Overrides the data type of the output arrays. A
                matching precision of the calculation should be
                chosen. For example, a *dtype* of ``'int32'`` is only
                allowed when the input values are integers.

             {{inplace: `bool`, optional}}

        :Returns:

            `Data` or `None`
                The element-wise square of the data, or `None` if the
                operation was in-place.

        **Examples**

        >>> d = cf.Data([[0, 1, 2.5, 3, 4]], 'K', mask=[[0, 0, 0, 1, 0]])
        >>> print(d.array)
        [[0.0 1.0 2.5 -- 4.0]]
        >>> e = d.square()
        >>> e
        <CF Data(1, 5): [[0.0, ..., 16.0]] K2>
        >>> print(e.array)
        [[0.0 1.0 6.25 -- 16.0]]

        """
        d = _inplace_enabled_define_and_cleanup(self)
        dx = d.to_dask_array()
        dx = da.square(dx, dtype=dtype)
        d._set_dask(dx)

        units = d.Units
        if units:
            d.override_units(units**2, inplace=True)

        return d

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    def sqrt(self, dtype=None, inplace=False):
        """Calculate the non-negative square root.

        .. versionadded:: TODODASKVER

        .. seealso:: `square`

        :Parameters:

            dtype: data-type, optional
                Overrides the data type of the output arrays. A
                matching precision of the calculation should be
                chosen. For example, a *dtype* of ``'int32'` is not
                allowed, even if the input values are perfect squares.

             {{inplace: `bool`, optional}}

        :Returns:

            `Data` or `None`
                The element-wise positive square root of the data, or
                `None` if the operation was in-place.

        **Examples**

        >>> d = cf.Data([[0, 1, 2, 3, 4]], 'K2', mask=[[0, 0, 0, 1, 0]])
        >>>print(d.array)
        [[0 1 2 -- 4]]
        >>> e = d.sqrt()
        >>> e
        <CF Data(1, 5): [[0.0, ..., 2.0]] K>
        >>> print(e.array)
        [[0.0 1.0 1.4142135623730951 -- 2.0]]

        Negative input values raise a warning but nonetheless result in NaN
        or, if there are already missing values, missing data:

        >>> import warnings
        >>> d = cf.Data([0, 1, -4])
        >>> print(d.array)
        [ 0  1 -4]
        >>> with warnings.catch_warnings():
        ...     warnings.simplefilter("ignore")
        ...     print(d.sqrt().array)
        ...
        [ 0.  1. nan]

        >>> d = cf.Data([0, 1, -4], mask=[1, 0, 0])
        >>> print(d.array)
        [-- 1 -4]
        >>> with warnings.catch_warnings():
        ...     warnings.simplefilter("ignore")
        ...     print(d.sqrt().array)
        ...
        [-- 1.0 --]

        """
        d = _inplace_enabled_define_and_cleanup(self)
        dx = d.to_dask_array()
        dx = da.sqrt(dx, dtype=dtype)
        d._set_dask(dx)

        units = d.Units
        if units:
            try:
                d.override_units(units**0.5, inplace=True)
            except ValueError as e:
                raise type(e)(
                    f"Incompatible units for taking a square root: {units!r}"
                )

        return d

    # ----------------------------------------------------------------
    # Aliases
    # ----------------------------------------------------------------
    @property
    def dtarray(self):
        """Alias for `datetime_array`"""
        return self.datetime_array

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    @_deprecated_kwarg_check("i")
    def maximum(
        self,
        axes=None,
        squeeze=False,
        mtol=1,
        split_every=None,
        inplace=False,
        i=False,
    ):
        """Alias for `max`"""
        return self.max(
            axes=axes,
            squeeze=squeeze,
            mtol=mtol,
            split_every=split_every,
            inplace=inplace,
            i=i,
        )

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    @_deprecated_kwarg_check("i")
    def minimum(
        self,
        axes=None,
        squeeze=False,
        mtol=1,
        split_every=None,
        inplace=False,
        i=False,
    ):
        """Alias for `min`"""
        return self.min(
            axes=axes,
            squeeze=squeeze,
            mtol=mtol,
            split_every=split_every,
            inplace=inplace,
            i=i,
        )

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    @_deprecated_kwarg_check("i")
    def sd(
        self,
        axes=None,
        squeeze=False,
        mtol=1,
        weights=None,
        ddof=0,
        split_every=None,
        inplace=False,
        i=False,
    ):
        """Alias for `std`"""
        return self.std(
            axes=axes,
            squeeze=squeeze,
            weights=weights,
            mtol=mtol,
            ddof=ddof,
            split_every=split_every,
            inplace=inplace,
            i=i,
        )

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    @_deprecated_kwarg_check("i")
    def standard_deviation(
        self,
        axes=None,
        squeeze=False,
        mtol=1,
        weights=None,
        ddof=0,
        split_every=None,
        inplace=False,
        i=False,
    ):
        """Alias for `std`"""
        return self.std(
            axes=axes,
            squeeze=squeeze,
            weights=weights,
            mtol=mtol,
            ddof=ddof,
            split_every=split_every,
            inplace=inplace,
            i=i,
        )

    @daskified(_DASKIFIED_VERBOSE)
    @_inplace_enabled(default=False)
    @_deprecated_kwarg_check("i")
    def variance(
        self,
        axes=None,
        squeeze=False,
        weights=None,
        mtol=1,
        ddof=0,
        split_every=None,
        inplace=False,
        i=False,
    ):
        """Alias for `var`"""
        return self.var(
            axes=axes,
            squeeze=squeeze,
            weights=weights,
            mtol=mtol,
            ddof=ddof,
            split_every=split_every,
            inplace=inplace,
            i=i,
        )


def _size_of_index(index, size=None):
    """Return the number of elements resulting in applying an index to a
    sequence.

    :Parameters:

        index: `slice` or `list` of `int`
            The index being applied to the sequence.

        size: `int`, optional
            The number of elements in the sequence being indexed. Only
            required if *index* is a slice object.

    :Returns:

        `int`
            The length of the sequence resulting from applying the index.

    **Examples**

    >>> _size_of_index(slice(None, None, -2), 10)
    5
    >>> _size_of_index([1, 4, 9])
    3

    """
    if isinstance(index, slice):
        # Index is a slice object
        start, stop, step = index.indices(size)
        div, mod = divmod(stop - start, step)
        if mod != 0:
            div += 1
        return div
    else:
        # Index is a list of integers
        return len(index)


def _broadcast(a, shape):
    """Broadcast an array to a given shape.

    It is assumed that ``len(array.shape) <= len(shape)`` and that the
    array is broadcastable to the shape by the normal numpy
    boradcasting rules, but neither of these things are checked.

    For example, ``d[...] = d._broadcast(e, d.shape)`` gives the same
    result as ``d[...] = e``

    :Parameters:

        a: numpy array-like

        shape: `tuple`

    :Returns:

        `numpy.ndarray`

    """
    # Replace with numpy.broadcast_to v1.10 ??/ TODO

    a_shape = np.shape(a)
    if a_shape == shape:
        return a

    tile = [(m if n == 1 else 1) for n, m in zip(a_shape[::-1], shape[::-1])]
    tile = shape[0 : len(shape) - len(a_shape)] + tuple(tile[::-1])

    return np.tile(a, tile)


def _where_broadcastable(data, x, name):
    """Check broadcastability for `where` assignments.

    Raises an exception if the result of broadcasting *data* and *x*
    together does not have the same shape as *data*.

    .. versionadded:: TODODASKVER

    .. seealso:: `where`

    :Parameters:

        data, x: `Data`
            The arrays to compare.

        name: `str`
            A name for *x* that is used in any exception error
            message.

    :Returns:

        `bool`
             If *x* is acceptably broadcastable to *data* then `True`
             is returned, otherwise a `ValueError` is raised.

    """
    ndim_x = x.ndim
    if not ndim_x:
        return True

    ndim_data = data.ndim
    if ndim_x > ndim_data:
        raise ValueError(
            f"where: Broadcasting the {name!r} parameter with {ndim_x} "
            f"dimensions would change the shape of the data with "
            f"{ndim_data} dimensions"
        )

    shape_x = x.shape
    shape_data = data.shape
    for n, m in zip(shape_x[::-1], shape_data[::-1]):
        if n != m and n != 1:
            raise ValueError(
                f"where: Broadcasting the {name!r} parameter with shape "
                f"{shape_x} would change the shape of the data with shape "
                f"{shape_data}"
            )

    return True


def _collapse(
    func,
    d,
    axis=None,
    weights=None,
    keepdims=True,
    mtol=1,
    ddof=None,
    split_every=None,
):
    """Collapse data in-place using a given funcion.

     .. versionadded:: TODODASKVER

     .. seealso:: `_parse_weights`

    :Parameters:

        func: callable
            The function that collapses the underlying `dask` array of
            *d*. Must have the minimum signature (parameters and
            default values) ``func(dx, axis=None, keepdims=False,
            mtol=None, split_every=None)`` (optionally including
            ``weights=None`` or ``ddof=None``), where ``dx`` is a the
            dask array contained in *d*.

        d: `Data`
            The data to be collapsed.

        axis: (sequence of) int, optional
            The axes to be collapsed. By default all axes are
            collapsed, resulting in output with size 1. Each axis is
            identified by its integer position. If *axes* is an empty
            sequence then the collapse is applied to each scalar
            element and the reuslt has the same shape as the input
            data.

        weights: data_like, `dict`, or `None`, optional
            Weights associated with values of the data. By default
            *weights* is `None`, meaning that all non-missing elements
            of the data have a weight of 1 and all missing elements
            have a weight of 0.

            If *weights* is a data_like object then it must be
            broadcastable to the array.

            If *weights* is a dictionary then each key specifies axes
            of the data (an `int` or `tuple` of `int`), with a
            corresponding value of data_like weights for those
            axes. The dimensions of a weights value must correspond to
            its key axes in the same order. Not all of the axes need
            weights assigned to them. The weights that will be used
            will be an outer product of the dictionary's values.

            However they are specified, the weights are internally
            broadcast to the shape of the data, and those weights that
            are missing data, or that correspond to the missing
            elements of the data, are assigned a weight of 0.

            For collapse functions that do not have a ``weights``
            parameter, *weights* must be `None`.

        keepdims: `bool`, optional
            By default, the axes which are collapsed are left in the
            result as dimensions with size one, so that the result
            will broadcast correctly against the input array. If set
            to False then collapsed axes are removed from the data.

        mtol: number, optional
            The sample size threshold below which collapsed values are
            set to missing data. It is defined as a fraction (between
            0 and 1 inclusive) of the contributing input data values.

            The default of *mtol* is 1, meaning that a missing datum
            in the output array occurs whenever all of its
            contributing input array elements are missing data.

            For other values, a missing datum in the output array
            occurs whenever more than ``100*mtol%`` of its
            contributing input array elements are missing data.

        ddof: number, optional
            The delta degrees of freedom. The number of degrees of
            freedom used in the calculation is (N-*ddof*) where N
            represents the number of non-missing elements.

            For collapse functions that do not have a ``ddof``
            parameter, *ddof* must be `None`.

        split_every: `int` or `dict`, optional
            Determines the depth of the recursive aggregation. See
            `dask.array.reduction` for details.

    :Returns:

        (`Data`, formatted weights)
            The collapsed data and the output of ``_parse_weights(d,
            weights, axis)``.

    """
    kwargs = {
        "axis": axis,
        "keepdims": keepdims,
        "split_every": split_every,
        "mtol": mtol,
    }

    weights = _parse_weights(d, weights, axis)
    if weights is not None:
        kwargs["weights"] = weights

    if ddof is not None:
        kwargs["ddof"] = ddof

    dx = d.to_dask_array()
    dx = func(dx, **kwargs)
    d._set_dask(dx)

    return d, weights


def _parse_weights(d, weights, axis=None):
    """Parse the weights input to `_collapse`.

     .. versionadded:: TODODASKVER

     .. seealso:: `_collapse`

    :Parameters:

        d: `Data`
            The data to be collapsed.

        weights: data_like or `dict`
            See `_collapse` for details.

        axis: (sequence of) `int`, optional
            See `_collapse` for details.

    :Returns:

        `Data` or `None`
            * If *weights* is a data_like object then they are
              returned unchanged as a `Data` object. It is up to the
              downstream functions to check if the weights can be
              broadcast to the data.

            * If *weights* is a dictionary then the dictionary
              values', i.e. the weights components, outer product is
              returned in `Data` object that is broadcastable to the
              data.

              If the dictionary is empty, or none of the axes defined
              by the keys correspond to collapse axes defined by
              *axis*, then then the collapse is unweighted and `None`
              is returned.

            Note that, in all cases, the returned weights are *not*
            modified to account for missing values in the data.

    **Examples**

    >>> d = cf.Data(np.arange(12)).reshape(4, 3)

    >>> _parse_weights(d, [1, 2, 1], (0, 1))
    <CF Data(3): [1, 2, 1]>

    >>> _parse_weights(d, [[1, 2, 1]], (0, 1))
    <CF Data(1, 3): [[1, 2, 1]]>

    >>> _parse_weights(d, {1: [1, 2, 1]}, (0, 1))
    <CF Data(1, 3): [[1, 2, 1]]>

    >>> print(_parse_weights(d, {0: [1, 2, 3, 4], 1: [1, 2, 1]}, (0, 1)))
    [[1 2 1]
     [2 4 2]
     [3 6 3]
     [4 8 4]]

    >>> print(cf.data.data._parse_weights(d, {}, (0, 1)))
    None

    >>> print(cf.data.data._parse_weights(d, {1: [1, 2, 1]}, 0))
    None

    """
    if weights is None:
        # No weights
        return

    if not isinstance(weights, dict):
        # Weights is data_like. Don't check broadcastability to d,
        # leave that to whatever uses the weights.
        return Data.asdata(weights)

    if not weights:
        # No weights (empty dictionary)
        return

    if axis is None:
        axis = tuple(range(d.ndim))
    else:
        axis = d._parse_axes(axis)

    weights = weights.copy()
    weights_axes = set()
    for key, value in tuple(weights.items()):
        del weights[key]
        key = d._parse_axes(key)
        if weights_axes.intersection(key):
            raise ValueError("Duplicate weights axis")

        weights[tuple(key)] = value
        weights_axes.update(key)

    if not weights_axes.intersection(axis):
        # No weights span collapse axes
        return

    # For each component, add missing dimensions as size 1.
    w = []
    shape = d.shape
    for key, value in weights.items():
        value = Data.asdata(value)

        # Make sure axes are in ascending order
        skey = tuple(sorted(key))
        if key != skey:
            value = value.transpose(skey)
            key = skey

        if not all(
            True if i in (j, 1) else False
            for i, j in zip(value.shape, [shape[i] for i in key])
        ):
            raise ValueError(
                f"Weights component for axes {tuple(key)} with shape "
                f"{value.shape} is not broadcastable to data with "
                f"shape {shape}"
            )

        new_shape = [n if i in key else 1 for i, n in enumerate(shape)]
        w.append(value.reshape(new_shape))

    # Return the product of the weights components, which will be
    # broadcastable to d
    return reduce(mul, w)
