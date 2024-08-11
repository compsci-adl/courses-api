# Courses API

Refer to the makefile for commandlines

## Setup

Ensure that all dependencies are installed:

```sh
poetry install
```

## Running the API Server

We use Uvicorn as the Asynchronous Server Gateway Interface (ASGI) to run our FastAPI application:

```sh
poetry run uvicorn src.db.server:app --reload
```

## Running an Example

To run the example script:

```sh
poetry run python src/main.py
```

### Scrape Course Data

**Note: The API is rate-limited and may time out.**

To scrape course data, run the following command:

```sh
poetry run python src/scraper/CourseScraper.py
```

## Running Tests

### Run All Tests

To run all tests:

```sh
poetry run python -m unittest discover -s tests
```

### Run a Specific Test

To run a specific test, such as `CourseScraper` or `DataFetcher`, and optionally show the data (for debugging/viewing purposes), use the following commands:

```sh
SHOW_DATA=true poetry run python -m unittest tests.unit.test_CourseScraper
```

or

```sh
SHOW_DATA=true poetry run python -m unittest tests.integration.test_DataFetcher
```
