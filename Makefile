.PHONY: install dev test lint build up down logs

install:
	pip install -e ".[dev]"

dev:
	uvicorn apps.api.main:app --reload

test:
	pytest

lint:
	black .
	flake8 .

build:
	docker-compose build

up:
	docker-compose up -d

down:
	docker-compose down

logs:
	docker-compose logs -f
