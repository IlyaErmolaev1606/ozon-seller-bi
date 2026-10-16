PY ?= python3
export PYTHONPATH := src

.PHONY: install demo test dashboard clean

install:
	$(PY) -m pip install -r requirements-dev.txt

demo:            ## сгенерировать синтетические данные, собрать витрины и дашборд
	$(PY) -m osbi.build --demo

test:
	$(PY) -m pytest -q

dashboard:       ## пересобрать витрины и дашборд на уже сгенерированных данных
	$(PY) -m osbi.build

clean:
	rm -rf build data/demo .pytest_cache
