PY = .venv/bin/python
METRO ?= dfw
export PYTHONPATH = pipeline

.PHONY: test layers tracks trace all

test:
	PYTHONPATH=pipeline $(PY) -m pytest -q

layers:
	PYTHONPATH=pipeline $(PY) -m survey.fetch_layers --metro $(METRO)

tracks:
	PYTHONPATH=pipeline $(PY) -m survey.fetch_tracks

trace:
	PYTHONPATH=pipeline $(PY) -m survey.georef

all:
	PYTHONPATH=pipeline $(PY) -m survey.run --metro $(METRO)
