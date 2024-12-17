from typing import TYPE_CHECKING, Any

import torch
from transformers import AutoModel, AutoTokenizer

from fast_forward.encoder.base import Encoder

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    import numpy as np
    from transformers import BatchEncoding
    from transformers.modeling_outputs import BaseModelOutput


class TransformerEncoder(Encoder):
    """Uses a pre-trained Transformer model for encoding.

    The outputs from the last hidden layer corresponding to the CLS token are used.
    """

    def __init__(
        self,
        model: "str | Path",
        device: str = "cpu",
        model_args: "Mapping[str, Any]" = {},
        tokenizer_args: "Mapping[str, Any]" = {},
        tokenizer_call_args: "Mapping[str, Any]" = {},
    ) -> None:
        """Create a Transformer encoder.

        :param model: Pre-trained transformer model (name or path).
        :param device: PyTorch device.
        :param model_args: Additional arguments for the model.
        :param tokenizer_args: Additional arguments for the tokenizer.
        :param tokenizer_call_args: Additional arguments for the tokenizer call.
        """
        super().__init__()
        self._model = AutoModel.from_pretrained(model, **model_args)
        self._model.to(device)
        self._model.eval()
        self._tokenizer = AutoTokenizer.from_pretrained(model, **tokenizer_args)
        self._device = device
        self._tokenizer_call_args = tokenizer_call_args

    def _get_tokenizer_inputs(self, texts: "Sequence[str]") -> list[str]:
        """Prepare input texts for tokenization.

        :param texts: The texts to encode.
        :return: The tokenizer inputs.
        """
        return list(texts)

    def _aggregate_model_outputs(
        self,
        model_outputs: "BaseModelOutput",
        model_inputs: "BatchEncoding",  # noqa: ARG002
    ) -> torch.Tensor:
        """Aggregate Transformer outputs using the CLS token (last hidden state).

        Encoders overriding this function may make use of `model_inputs`.

        :param model_outputs: The Transformer outputs.
        :param model_inputs: The Transformer inputs (unused).
        :return: The CLS token representations from the last hidden state.
        """
        return model_outputs.last_hidden_state[:, 0]

    def _encode(self, texts: "Sequence[str]") -> "np.ndarray":
        model_inputs = self._tokenizer(
            self._get_tokenizer_inputs(texts),
            return_tensors="pt",
            **self._tokenizer_call_args,
        ).to(self._device)

        with torch.no_grad():
            model_outputs = self._model(**model_inputs)
            return (
                self._aggregate_model_outputs(model_outputs, model_inputs)
                .detach()
                .cpu()
                .numpy()
            )


class TCTColBERTQueryEncoder(TransformerEncoder):
    """Pre-trained TCT-ColBERT query encoder.

    Adapted from Pyserini:
    https://github.com/castorini/pyserini/blob/310c828211bb3b9528cfd59695184c80825684a2/pyserini/encode/_tct_colbert.py#L72
    """

    def __init__(
        self,
        model: "str | Path" = "castorini/tct_colbert-msmarco",
        device: str = "cpu",
        max_length: int = 36,
    ) -> None:
        """Create a TCT-ColBERT query encoder.

        :param model: Pre-trained TCT-ColBERT model (name or path).
        :param device: PyTorch device.
        :param max_length: Maximum number of tokens per query.
        """
        self._max_length = max_length
        super().__init__(
            model,
            device=device,
            tokenizer_call_args={
                "max_length": max_length,
                "truncation": True,
                "add_special_tokens": False,
            },
        )

    def _get_tokenizer_inputs(self, texts: "Sequence[str]") -> list[str]:
        return ["[CLS] [Q] " + q + "[MASK]" * self._max_length for q in texts]

    def _aggregate_model_outputs(
        self,
        model_outputs: "BaseModelOutput",
        model_inputs: "BatchEncoding",  # noqa: ARG002
    ) -> torch.Tensor:
        embeddings = model_outputs.last_hidden_state[:, 4:, :]
        return torch.mean(embeddings, dim=-2)


class TCTColBERTDocumentEncoder(TransformerEncoder):
    """Pre-trained TCT-ColBERT document encoder.

    Adapted from Pyserini:
    https://github.com/castorini/pyserini/blob/310c828211bb3b9528cfd59695184c80825684a2/pyserini/encode/_tct_colbert.py#L27
    """

    def __init__(
        self,
        model: "str | Path" = "castorini/tct_colbert-msmarco",
        device: str = "cpu",
        max_length: int = 512,
    ) -> None:
        """Create a TCT-ColBERT document encoder.

        :param model: Pre-trained TCT-ColBERT model (name or path).
        :param device: PyTorch device.
        :param max_length: Maximum number of tokens per document.
        """
        self._max_length = max_length
        super().__init__(
            model,
            device=device,
            tokenizer_call_args={
                "max_length": max_length,
                "padding": True,
                "truncation": True,
                "add_special_tokens": False,
            },
        )

    def _get_tokenizer_inputs(self, texts: "Sequence[str]") -> list[str]:
        return ["[CLS] [D] " + d for d in texts]

    def _aggregate_model_outputs(
        self,
        model_outputs: "BaseModelOutput",
        model_inputs: "BatchEncoding",
    ) -> torch.Tensor:
        token_embeddings = model_outputs.last_hidden_state[:, 4:, :]
        input_mask_expanded = (
            model_inputs.attention_mask[:, 4:]
            .unsqueeze(-1)
            .expand(token_embeddings.size())
            .float()
        )
        sum_embeddings = torch.sum(token_embeddings * input_mask_expanded, 1)
        sum_mask = torch.clamp(input_mask_expanded.sum(1), min=1e-9)
        return sum_embeddings / sum_mask
