import abc
from functools import reduce
from operator import mul

from ...functions import inspect as cf_inspect


class CompressedSubarray(abc.ABC):
    """Abstract base class for a compressed sub-array container."""

    def __init__(self, array, shape, compression):
        """**Initialization**

        :Parameters:

            array:

            shape: `tuple`
                The shape of the uncompressed array

            compression: `dict`

        """
        # DO NOT CHANGE IN PLACE
        self.array = array

        # DO NOT CHANGE IN PLACE
        self.compression = compression

        # DO NOT CHANGE IN PLACE
        self.shape = tuple(shape)

        # DO NOT CHANGE IN PLACE
        self.ndim = len(shape)

        # DO NOT CHANGE IN PLACE
        self.size = reduce(mul, shape, 1)

    @abc.abstractmethod
    def __getitem__(self, indices):
        """x.__getitem__(indices) <==> x[indices]

        Returns a numpy array.

        """
        raise NotImplementedError()  # pragma: no cover

    def __repr__(self):
        """x.__repr__() <==> repr(x)"""
        array = self.array
        shape = str(array.shape)
        shape = shape.replace(",)", ")")

        return f"<CF {self.__class__.__name__}{shape}: {array}>"

    # ----------------------------------------------------------------
    # Dask attributes
    # ----------------------------------------------------------------
    @property
    def dask_lock(self):
        """TODODASKDOCS."""
        return self.array.dask_lock

    @property
    def dask_asarray(self):
        """TODODASKDOCS."""
        return False

    # ----------------------------------------------------------------
    # Attributes
    # ----------------------------------------------------------------
    @property
    def dtype(self):
        return self.array.dtype

    @property
    def file(self):
        """The file on disk which contains the compressed array, or
        `None` of the array is in memory.

        **Examples**

         >>> self.file
         '/home/foo/bar.nc'

        """
        return getattr(self.array, "file", None)

    # ----------------------------------------------------------------
    # Methods
    # ----------------------------------------------------------------
    def close(self):
        """Close all referenced open files.

        :Returns:

            `None`

        **Examples**

        >>> f.close()

        """
        if self.on_disk():
            self.array.close()

    def copy(self):
        """Replace the abstract base class with a deep copy."""
        C = self.__class__
        new = C.__new__(C)
        new.__dict__ = self.__dict__.copy()
        return new

    def inspect(self):
        """Inspect the object for debugging.

        .. seealso:: `cf.inspect`

        :Returns:

            `None`

        """
        print(cf_inspect(self))

    def on_disk(self):
        """True if and only if the compressed array is on disk as
        opposed to in memory.

        **Examples**

        >>> a.on_disk()
        True

        """
        return not hasattr(self.array, "__array_interface__")
