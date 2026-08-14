.PHONY: install test compile smoke

install:
	python -m pip install -e .

compile:
	python -m compileall -q src

test: compile
	python -m unittest discover -s tests -v

smoke:
	AGENTNAVI_HOME=$$(mktemp -d) python -m agentnavi init
