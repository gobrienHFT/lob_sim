PY ?= python
ENV ?= .env.example
FIXTURE ?= docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson
RECORDED_FIXTURE ?= docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson
BENCHMARK_JSON ?= outputs/futures_benchmark.json

.PHONY: setup test verify-artifacts inspect-fixture replay-fixture simulate-fixture benchmark-fixture sweep-fixture refresh-artifacts

setup:
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements.txt

test:
	$(PY) -m pytest

verify-artifacts:
	$(PY) scripts/verify_committed_artifacts.py

inspect-fixture:
	$(PY) -m lob_sim.cli inspect --file $(FIXTURE)

replay-fixture:
	$(PY) -m lob_sim.cli --env $(ENV) replay --file $(FIXTURE)

simulate-fixture:
	$(PY) -m lob_sim.cli --env $(ENV) simulate --file $(FIXTURE)

benchmark-fixture:
	$(PY) experiments/benchmark_futures_replay.py --file $(RECORDED_FIXTURE) --env $(ENV) --json-out $(BENCHMARK_JSON)

sweep-fixture:
	$(PY) experiments/sweep_futures_parameters.py --file $(RECORDED_FIXTURE) --env $(ENV) --out-dir outputs/futures_sweeps

refresh-artifacts:
	$(PY) scripts/refresh_futures_showcase.py
	$(PY) scripts/refresh_futures_recorded_case.py
	$(PY) scripts/refresh_futures_strategy_profile_reference.py
	$(PY) scripts/refresh_futures_benchmark_reference.py
