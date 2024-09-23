.PHONY: install test unit integration server scrape

install:
	poetry install

server:
	poetry run uvicorn src.db.server:app --reload

test:
	poetry run python -m unittest discover -s tests

test-scraper:
	SHOW_DATA=true poetry run python -m unittest tests.test_CourseScraper

test-fetcher:
	SHOW_DATA=true poetry run python -m unittest tests.test_DataFetcher

scrape:
	poetry run python src/scraper/CourseScraper.py
