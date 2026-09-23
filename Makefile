# Короткие псевдонимы. Вся логика — в scripts/dev.py, чтобы команды работали и без make
# (на Windows его обычно нет). `python scripts/dev.py <цель>` делает ровно то же самое.
.PHONY: setup backend frontend test typecheck check verify

PYTHON ?= python

setup:            ## один раз после clone
	$(PYTHON) scripts/dev.py setup

backend:          ## http://localhost:8000/api/health
	$(PYTHON) scripts/dev.py backend

frontend:         ## http://localhost:3000
	$(PYTHON) scripts/dev.py frontend

test:
	$(PYTHON) scripts/dev.py test

typecheck:
	$(PYTHON) scripts/dev.py typecheck

check:            ## гонять перед каждым коммитом
	$(PYTHON) scripts/dev.py check

verify:           ## сквозная проверка запущенного приложения
	$(PYTHON) scripts/dev.py verify
