"""
.. include:: ../docs/util.md
"""

from collections.abc import Callable

import numpy as np
import pandas as pd
from tqdm import tqdm

from fast_forward.index import Index
from fast_forward.ranking import Ranking


def to_ir_measures(ranking: Ranking) -> pd.DataFrame:
    """Return a ranking as a data frame suitable for the ir-measures library.

    :param ranking: The input ranking.
    :return: The data frame.
    """
    return ranking._df[["q_id", "id", "score"]].rename(
        columns={"q_id": "query_id", "id": "doc_id"}
    )


def cos_dist(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine distance of two vectors.

    :param a: First vector.
    :param b: Second vector.
    :return: Cosine distance.
    """
    assert len(a.shape) == len(b.shape) == 1
    return float(1 - np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def create_coalesced_index(
    source_index: Index,
    target_index: Index,
    delta: float,
    distance_function: Callable[[np.ndarray, np.ndarray], float] = cos_dist,
    batch_size: int | None = None,
) -> None:
    """Create a compressed index using sequential coalescing.

    :param source_index: The source index. Should contain multiple vectors for each document.
    :param target_index: The target index. Must be empty.
    :param delta: The coalescing threshold.
    :param distance_function: The distance function.
    :param batch_size: Use batches instead of adding all vectors at the end.
    """
    assert len(target_index.doc_ids) == 0
    batch_size = batch_size or len(source_index.doc_ids)

    def _coalesce(P):
        P_new = []
        A = []
        A_avg = np.empty(())
        first_iteration = True
        for v in P:
            if first_iteration:
                first_iteration = False
            elif distance_function(v, A_avg) >= delta:
                P_new.append(A_avg)
                A = []
            A.append(v)
            A_avg = np.mean(A, axis=0)
        P_new.append(A_avg)
        return P_new

    vectors, doc_ids = [], []
    for doc_id in tqdm(source_index.doc_ids):
        # check if batch is full
        if len(vectors) == batch_size:
            target_index.add(np.array(vectors), doc_ids=doc_ids)
            vectors, doc_ids = [], []

        v_old, _ = source_index._get_vectors([doc_id])
        v_new = _coalesce(v_old)
        vectors.extend(v_new)
        doc_ids.extend([doc_id] * len(v_new))

    if len(vectors) > 0:
        target_index.add(np.array(vectors), doc_ids=doc_ids)

    assert source_index.doc_ids == target_index.doc_ids
