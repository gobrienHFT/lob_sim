PY ?= python
ENV ?= .env.example
FIXTURE ?= docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson
RECORDED_FIXTURE ?= docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson
BENCHMARK_JSON ?= outputs/futures_benchmark.json
DETERMINISM_JSON ?= outputs/futures_determinism.json

.PHONY: setup test verify-artifacts check-whitespace ci inspect-fixture replay-fixture simulate-fixture benchmark-fixture determinism-fixture sweep-fixture refresh-artifacts

setup:
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements.txt

test:
	$(PY) -m pytest

verify-artifacts:
	$(PY) scripts/verify_committed_artifacts.py

check-whitespace:
	git diff --check

ci: test verify-artifacts check-whitespace

inspect-fixture:
	$(PY) -m lob_sim.cli inspect --file $(FIXTURE)

replay-fixture:
	$(PY) -m lob_sim.cli --env $(ENV) replay --file $(FIXTURE)

simulate-fixture:
	$(PY) -m lob_sim.cli --env $(ENV) simulate --file $(FIXTURE)

benchmark-fixture:
	$(PY) experiments/benchmark_futures_replay.py --file $(RECORDED_FIXTURE) --env $(ENV) --json-out $(BENCHMARK_JSON)

determinism-fixture:
	$(PY) scripts/check_futures_determinism.py --file $(FIXTURE) --env $(ENV) --json-out $(DETERMINISM_JSON)

sweep-fixture:
	$(PY) experiments/sweep_futures_parameters.py --file $(RECORDED_FIXTURE) --env $(ENV) --out-dir outputs/futures_sweeps

refresh-artifacts:
	$(PY) scripts/refresh_futures_reviewer_artifacts.py
