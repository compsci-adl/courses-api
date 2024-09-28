run-server:
	fastapi run src/server.py

run-scraper:
	poetry run src/scraper

test:
	cd tests && poetry run pytest
