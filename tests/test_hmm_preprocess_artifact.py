from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from lob_sim.regime.artifact import FrozenRegimeModel, load_model, save_model
from lob_sim.regime.features import FEATURE_NAMES, FeatureSpec
from lob_sim.regime.filter import ForwardFilter
from lob_sim.regime.model import GaussianHMMParameters
from lob_sim.regime.preprocess import TrainOnlyScaler
from lob_sim.regime.validation import canonical_json, identity, strict_json


def _scaler() -> TrainOnlyScaler:
    return TrainOnlyScaler.fit_training(
        [(0.0, 5.0), (1.0, 5.0), (2.0, 5.0)],
        feature_names=("x", "constant"),
        feature_identity="a" * 64,
        clip_quantiles=None,
    )


def _artifact() -> FrozenRegimeModel:
    features = FeatureSpec()
    d = len(FEATURE_NAMES)
    scaler = TrainOnlyScaler.fit_training(
        [tuple(float(index) for _ in range(d)) for index in range(4)],
        feature_names=FEATURE_NAMES,
        feature_identity=features.digest,
    )
    parameters = GaussianHMMParameters(
        (0.5, 0.5),
        ((0.9, 0.1), (0.1, 0.9)),
        (tuple([-1.0] * d), tuple([1.0] * d)),
        (tuple([1.0] * d), tuple([1.0] * d)),
    )
    return FrozenRegimeModel(
        features, scaler, parameters, canonical_json({"purpose": "synthetic_unit_test", "seed": 7})
    )


def test_training_statistics_and_constant_features_are_explicit() -> None:
    scaler = _scaler()
    assert scaler.center == (1.0, 5.0)
    assert scaler.scale == pytest.approx(((2 / 3) ** 0.5, 1.0))
    assert scaler.constant_features == (False, True)
    assert scaler.transform(
        (1.0, 5.0), feature_names=scaler.feature_names, feature_identity=scaler.feature_identity
    ) == (0.0, 0.0)
    assert scaler.training_rows == 3
    assert TrainOnlyScaler.from_dict(scaler.as_dict()) == scaler


def test_validation_and_test_values_cannot_refit_training_scaler() -> None:
    scaler = _scaler()
    before = scaler.as_dict()
    for value in (-100.0, 100.0, 999.0):
        scaler.transform((value, 10.0), feature_names=scaler.feature_names, feature_identity=scaler.feature_identity)
    assert scaler.as_dict() == before


def test_clipping_uses_training_quantiles_before_population_scaling() -> None:
    scaler = TrainOnlyScaler.fit_training(
        [(0.0,), (1.0,), (2.0,), (100.0,)],
        feature_names=("x",),
        feature_identity="f" * 64,
        clip_quantiles=(0.25, 0.75),
    )
    assert scaler.lower == (0.75,)
    assert scaler.upper == (26.5,)
    assert scaler.center == ((0.75 + 1 + 2 + 26.5) / 4,)
    assert scaler.clip_quantiles == (0.25, 0.75)
    assert TrainOnlyScaler.from_dict(scaler.as_dict()) == scaler
    assert scaler.transform((1e6,), feature_names=("x",), feature_identity="f" * 64) == scaler.transform(
        (26.5,), feature_names=("x",), feature_identity="f" * 64
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"feature_names": ("constant", "x"), "feature_identity": "a" * 64},
        {"feature_names": ("x", "constant"), "feature_identity": "b" * 64},
    ],
)
def test_scaler_rejects_feature_reorder_and_formula_identity_change(kwargs: dict) -> None:
    with pytest.raises(ValueError, match="identity"):
        _scaler().transform((1.0, 5.0), **kwargs)


@pytest.mark.parametrize("rows", [[], [(1.0,)], [(float("nan"), 5.0)], [(True, 5.0)], [(1e308, 0.0), (-1e308, 0.0)]])
def test_bad_training_rows_are_rejected(rows) -> None:
    with pytest.raises(ValueError):
        TrainOnlyScaler.fit_training(rows, feature_names=("x", "y"), feature_identity="a" * 64, clip_quantiles=None)


def test_model_artifact_roundtrip_and_independent_filter_loading(tmp_path: Path) -> None:
    artifact = _artifact()
    path = tmp_path / "model.json"
    save_model(path, artifact)
    loaded = load_model(path)
    assert loaded == artifact
    assert loaded.model_sha256 == artifact.model_sha256
    assert not path.with_name("model.json.partial").exists()
    observations = tuple([0.7] * len(FEATURE_NAMES))
    assert ForwardFilter(loaded.parameters).update(observations) == ForwardFilter(artifact.parameters).update(
        observations
    )
    exported = loaded.as_dict()
    exported["provenance"]["seed"] = 999
    assert loaded.as_dict()["provenance"]["seed"] == 7
    with pytest.raises(FileExistsError):
        save_model(path, artifact)
    assert load_model(path) == artifact


def test_artifact_existing_partial_is_preserved(tmp_path: Path) -> None:
    path = tmp_path / "model.json"
    partial = path.with_name("model.json.partial")
    partial.write_bytes(b"existing evidence")
    with pytest.raises(FileExistsError):
        save_model(path, _artifact())
    assert partial.read_bytes() == b"existing evidence"
    assert not path.exists()


def test_artifact_checksum_corruption_is_rejected() -> None:
    data = _artifact().as_dict()
    data["parameters"]["means"][0][0] = 100.0
    with pytest.raises(ValueError, match="SHA-256"):
        FrozenRegimeModel.from_dict(data)


@pytest.mark.parametrize(
    "kind", ["schema", "covariance", "dimensions", "feature", "unknown", "probability", "provenance", "boolean_count"]
)
def test_checksums_do_not_make_semantically_invalid_artifacts_valid(kind: str) -> None:
    data = _artifact().as_dict()
    if kind == "schema":
        data["schema_version"] = "future_unknown_schema"
    elif kind == "covariance":
        data["parameters"]["covariance_type"] = "full"
    elif kind == "dimensions":
        data["parameters"]["means"][0].pop()
    elif kind == "feature":
        data["features"]["feature_formulas"][0] = "future_return"
    elif kind == "unknown":
        data["unexpected"] = "value"
    elif kind == "probability":
        data["parameters"]["start"] = [0.1, 0.1]
    elif kind == "boolean_count":
        data["parameters"]["state_count"] = True
    else:
        data["provenance"] = []
    data["model_sha256"] = identity({key: value for key, value in data.items() if key != "model_sha256"})
    with pytest.raises(ValueError):
        FrozenRegimeModel.from_dict(data)


@pytest.mark.parametrize("text", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e999}'])
def test_safe_json_rejects_duplicates_and_nonfinite_numbers(text: str) -> None:
    with pytest.raises(ValueError):
        strict_json(text)


def test_model_rejects_preprocessor_or_model_dimension_mismatch() -> None:
    model = _artifact()
    with pytest.raises(ValueError, match="identity"):
        replace(model, scaler=replace(model.scaler, feature_identity="0" * 64))
    with pytest.raises(ValueError, match="dimension"):
        replace(
            model,
            parameters=GaussianHMMParameters((0.5, 0.5), ((0.9, 0.1), (0.1, 0.9)), ((0.0,), (1.0,)), ((1.0,), (1.0,))),
        )


def test_serialized_artifact_is_finite_json_and_feature_spec_rejects_boolean_time() -> None:
    assert json.loads(canonical_json(_artifact().as_dict()))["parameters"]["covariance_type"] == "diag"
    with pytest.raises(ValueError):
        FeatureSpec(interval_ns=True)


def test_artifact_publication_race_preserves_existing_final_and_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import lob_sim.regime.artifact as artifacts

    target = tmp_path / "model.json"

    def racing_link(source, destination) -> None:
        Path(destination).write_bytes(b"other publisher's evidence")
        raise FileExistsError(destination)

    monkeypatch.setattr(artifacts.os, "link", racing_link)
    with pytest.raises(FileExistsError):
        save_model(target, _artifact())
    assert target.read_bytes() == b"other publisher's evidence"
    assert target.with_name("model.json.partial").is_file()


def test_failed_artifact_flush_cannot_publish_a_final_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import lob_sim.regime.artifact as artifacts

    def fail_flush(_descriptor: int) -> None:
        raise OSError("injected disk failure")

    monkeypatch.setattr(artifacts.os, "fsync", fail_flush)
    target = tmp_path / "model.json"
    with pytest.raises(OSError, match="disk failure"):
        save_model(target, _artifact())
    assert not target.exists()
    assert target.with_name("model.json.partial").is_file()


def test_model_read_is_bounded_even_when_file_is_large(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import lob_sim.regime.artifact as artifacts

    monkeypatch.setattr(artifacts, "MAX_ARTIFACT_BYTES", 32)
    target = tmp_path / "model.json"
    target.write_bytes(b" " * 33)
    with pytest.raises(ValueError, match="size limit"):
        load_model(target)


@pytest.mark.parametrize("quantiles", [(0.9, 0.1), (-0.1, 1.0), (0.0, 1.1), (True, 1.0)])
def test_invalid_quantile_configuration_is_rejected(quantiles) -> None:
    with pytest.raises(ValueError):
        TrainOnlyScaler.fit_training(
            [(1.0,), (2.0,)], feature_names=("x",), feature_identity="a" * 64, clip_quantiles=quantiles
        )
