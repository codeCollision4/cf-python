import cfdm

from .mixin import ArrayMixin


class RaggedIndexedArray(ArrayMixin, cfdm.RaggedIndexedArray):
    """An underlying indexed ragged array.

    A collection of features stored using an indexed ragged array
    combines all features along a single dimension (the "sample
    dimension") such that the values of each feature in the collection
    are interleaved.

    The information needed to uncompress the data is stored in an
    "index variable" that specifies the feature that each element of
    the sample dimension belongs to.

    It is assumed that the compressed dimension is the left-most
    dimension in the compressed array.

    See CF section 9 "Discrete Sampling Geometries".

    .. versionadded:: 3.0.0

    """

    def __repr__(self):
        """Called by the `repr` built-in function.

        x.__repr__() <==> repr(x)

        .. versionadded:: 3.0.0

        """
        return super().__repr__().replace("<", "<CF ", 1)
