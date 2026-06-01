PY ?= python
ENV ?= .env.example
FIXTURE ?= docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson
RECORDED_FIXTURE ?= docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson
BENCHMARK_JSON ?= outputs/futures_benchmark.json
DETERMINISM_JSON ?= outputs/futures_determinism.json
LATENCY_SWEEP_DIR ?= outputs/futures_latency_sweeps
AUDIT_PACK ?= docs/sample_outputs/futures_replay_walkthrough
MYPY_TARGETS ?= lob_sim/book lob_sim/replay lob_sim/record lob_sim/sim/fill_model.py lob_sim/sim/engine.py lob_sim/sim/metrics.py lob_sim/sim/run_manifest.py lob_sim/sim/mm_strategy.py

.PHONY: setup test type-check lint format-check verify-artifacts check-whitespace ci reviewer-gate inspect-fixture replay-fixture simulate-fixture audit-fixture audit-futures-packs benchmark-fixture determinism-fixture sweep-fixture latency-sweep-fixture refresh-artifacts

setup:
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements.txt

test:
	$(PY) -m pytest

type-check:
	$(PY) -m mypy $(MYPY_TARGETS)

lint:
	$(PY) -m ruff check .

format-check:
	$(PY) -m ruff format --check .

verify-artifacts:
	$(PY) scripts/verify_committed_artifacts.py

check-whitespace:
	git diff --check

ci: reviewer-gate

reviewer-gate:
	$(PY) scripts/reviewer_gate.py --python $(PY) --file $(FIXTURE) --recorded-file $(RECORDED_FIXTURE) --env $(ENV) --determinism-json $(DETERMINISM_JSON) --benchmark-json $(BENCHMARK_JSON)

inspect-fixture:
	$(PY) -m lob_sim.cli inspect --file $(FIXTURE)

replay-fixture:
	$(PY) -m lob_sim.cli --env $(ENV) replay --file $(FIXTURE)

simulate-fixture:
	$(PY) -m lob_sim.cli --env $(ENV) simulate --file $(FIXTURE)

audit-fixture:
	$(PY) scripts/audit_futures_pack.py --pack $(AUDIT_PACK)

audit-futures-packs:
	$(PY) scripts/audit_futures_pack.py --committed-futures

benchmark-fixture:
	$(PY) experiments/benchmark_futures_replay.py --file $(RECORDED_FIXTURE) --env $(ENV) --mode all --pack docs/sample_outputs/futures_stress_case --json-out $(BENCHMARK_JSON)

determinism-fixture:
	$(PY) scripts/check_futures_determinism.py --file $(FIXTURE) --env $(ENV) --json-out $(DETERMINISM_JSON)

sweep-fixture:
	$(PY) experiments/sweep_futures_parameters.py --file $(RECORDED_FIXTURE) --env $(ENV) --out-dir outputs/futures_sweeps

latency-sweep-fixture:
	$(PY) experiments/sweep_futures_latency.py --file $(RECORDED_FIXTURE) --env $(ENV) --out-dir $(LATENCY_SWEEP_DIR)

refresh-artifacts:
	$(PY) scripts/refresh_futures_reviewer_artifacts.py
