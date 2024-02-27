import logging
from pathlib import Path
from typing import Iterable, List, Sequence, Set, Tuple, Union

import h5py
import numpy as np

import fast_forward
from fast_forward.encoder import QueryEncoder
from fast_forward.index import Index, Mode

LOGGER = logging.getLogger(__name__)


class OnDiskIndex(Index):
    """Fast-Forward index that is read from disk (HDF5)."""

    def __init__(
        self,
        index_file: Path,
        dim: int,
        encoder: QueryEncoder = None,
        mode: Mode = Mode.PASSAGE,
        encoder_batch_size: int = 32,
        init_size: int = 2**14,
        resize_min_val: int = 2**10,
        hdf5_chunk_size: int = None,
        dtype: np.dtype = np.float32,
        overwrite: bool = False,
    ) -> None:
        """Constructor.

        Args:
            index_file (Path): Index file to create (or overwrite).
            dim (int): Vector dimension.
            encoder (QueryEncoder, optional): Query encoder. Defaults to None.
            mode (Mode, optional): Ranking mode. Defaults to Mode.PASSAGE.
            encoder_batch_size (int, optional): Batch size for query encoder. Defaults to 32.
            init_size (int, optional): Initial size to allocate (number of vectors). Defaults to 2**14.
            resize_min_val (int, optional): Minimum number of vectors to increase index size by. Defaults to 2**10.
            hdf5_chunk_size (int, optional): Override chunk size used by HDF5. Defaults to None.
            dtype (np.dtype, optional): Vector dtype. Defaults to np.float32.
            overwrite (bool, optional): Overwrite index file if it exists. Defaults to False.

        Raises:
            ValueError: When the file exists and `overwrite=False`.
        """
        if index_file.exists() and not overwrite:
            raise ValueError(f"File {index_file} exists")

        super().__init__(encoder, mode, encoder_batch_size)
        self._index_file = index_file.absolute()
        self._resize_min_val = resize_min_val

        with h5py.File(self._index_file, "w") as fp:
            fp.create_dataset(
                "vectors",
                (init_size, dim),
                dtype,
                maxshape=(None, dim),
                chunks=True if hdf5_chunk_size is None else (hdf5_chunk_size, dim),
            )
            fp["vectors"].attrs["num_vectors"] = 0
            fp.attrs["ff_version"] = fast_forward.__version__

    def __len__(self) -> int:
        with h5py.File(self._index_file, "r") as fp:
            return fp["vectors"].attrs["num_vectors"]

    @property
    def dim(self) -> int:
        with h5py.File(self._index_file, "r") as fp:
            return fp["vectors"].shape[1]

    def _add(
        self,
        vectors: np.ndarray,
        doc_ids: Sequence[Union[str, None]],
        psg_ids: Sequence[Union[str, None]],
    ) -> None:
        with h5py.File(self._index_file, "a") as fp:
            num_new_vecs = vectors.shape[0]
            capacity = fp["vectors"].shape[0]

            # check if we have enough space, resize if necessary
            cur_num_vectors = fp["vectors"].attrs["num_vectors"]
            space_left = capacity - cur_num_vectors
            if num_new_vecs > space_left:
                new_size = max(
                    capacity + num_new_vecs - space_left, self._resize_min_val
                )
                LOGGER.debug(f"resizing index from {capacity} to {new_size}")
                fp["vectors"].resize(new_size, axis=0)

            # add new vectors
            fp["vectors"][cur_num_vectors : cur_num_vectors + num_new_vecs] = vectors
            fp["vectors"].attrs["num_vectors"] += num_new_vecs

            # add IDs
            for i, (doc_id, psg_id) in enumerate(zip(doc_ids, psg_ids)):
                if doc_id is not None:
                    ds = fp.require_dataset(
                        f"/ids/doc/{doc_id}", (), dtype=h5py.vlen_dtype(np.uint)
                    )
                    ds[()] = np.append(ds[()], [cur_num_vectors + i])

                if psg_id is not None:
                    ds = fp.require_dataset(f"/ids/psg/{psg_id}", (), dtype=np.uint)
                    ds[()] = cur_num_vectors + i

    def _get_doc_ids(self) -> Set[str]:
        with h5py.File(self._index_file, "r") as fp:
            if "/ids/doc" not in fp:
                return set()
            return set(fp["/ids/doc"].keys())

    def _get_psg_ids(self) -> Set[str]:
        with h5py.File(self._index_file, "r") as fp:
            if "/ids/psg" not in fp:
                return set()
            return set(fp["/ids/psg"].keys())

    def _get_vectors(self, ids: Iterable[str]) -> Tuple[np.ndarray, List[List[int]]]:
        result_vectors = []
        id_idxs = []
        c = 0
        with h5py.File(self._index_file, "r") as fp:
            for id in ids:
                if self.mode in (Mode.MAXP, Mode.AVEP) and id in fp["/ids/doc"]:
                    idxs = fp[f"/ids/doc/{id}"][()]
                elif self.mode == Mode.FIRSTP and id in fp["/ids/doc"]:
                    idxs = [fp[f"/ids/doc/{id}"][()][0]]
                elif self.mode == Mode.PASSAGE and id in fp["/ids/psg"]:
                    idxs = [fp[f"/ids/psg/{id}"][()]]
                else:
                    LOGGER.warning(f"no vectors for {id}")
                    idxs = []

                result_vectors.append(fp["vectors"][idxs])
                id_idxs.append(list(range(c, c + len(idxs))))
                c += len(idxs)
            return np.concatenate(result_vectors), id_idxs

    @classmethod
    def load(
        cls,
        index_file: Path,
        encoder: QueryEncoder = None,
        mode: Mode = Mode.PASSAGE,
        encoder_batch_size: int = 32,
        resize_min_val: int = 2**10,
    ) -> "OnDiskIndex":
        """Open an existing index on disk.

        Args:
            index_file (Path): Index file to open.
            encoder (QueryEncoder, optional): Query encoder. Defaults to None.
            mode (Mode, optional): Ranking mode. Defaults to Mode.PASSAGE.
            encoder_batch_size (int, optional): Batch size for query encoder. Defaults to 32.
            resize_min_val (int, optional): Minimum number of vectors to increase index size by. Defaults to 2**10.

        Returns:
            OnDiskIndex: The index.
        """
        index = cls.__new__(cls)
        super(OnDiskIndex, index).__init__(encoder, mode, encoder_batch_size)
        index._index_file = index_file.absolute()
        index._resize_min_val = resize_min_val
        return index
