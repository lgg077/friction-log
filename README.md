# Friction Log

Friction Log is a beginner-friendly Flask web app for tracking repeated workplace frustrations and deciding which problems should be solved first.

## What Version 1 Includes

- A dashboard that lists every frustration entry
- A form to create a new entry
- Edit and delete actions
- Filters for department and status
- Automatic priority scoring
- Persistent storage with SQLite

## How Priority Works

Each entry gets a priority score using this formula:

`pain score x frequency score x estimated hours lost`

Frequency is converted into a number behind the scenes:

- Rarely = 1
- Monthly = 2
- Weekly = 3
- Several times a week = 4
- Daily = 5

## Project Structure

```text
friction-log/
|-- app.py
|-- requirements.txt
|-- README.md
|-- instance/
|-- static/
|   `-- css/
|       `-- styles.css
|-- templates/
|   |-- base.html
|   |-- dashboard.html
|   |-- frustration_form.html
|   `-- delete_confirmation.html
`-- tests/
    `-- test_app.py
```

## Setup

### Fastest option with uv

`uv` is already available in this workspace and is the quickest way to run the app without manually creating a virtual environment.

1. Open a terminal in the `friction-log` folder.
2. Start the app:

```powershell
uv run --with Flask==3.0.3 app.py
```

3. Open the local address shown in the terminal, usually `http://127.0.0.1:5000`.

### Standard Python setup

If you prefer the more traditional beginner workflow, use these steps instead.

1. Open a terminal in the `friction-log` folder.
2. Create a virtual environment:

```powershell
python -m venv .venv
```

3. Activate it:

```powershell
.\.venv\Scripts\Activate.ps1
```

4. Install the dependency:

```powershell
pip install -r requirements.txt
```

## Run the App

```powershell
python app.py
```

Then open the local address shown in the terminal, usually `http://127.0.0.1:5000`.

The database file will be created automatically in the `instance` folder the first time the app runs.

## Run the Tests

```powershell
uv run --with Flask==3.0.3 python -m unittest discover -s tests
```

## Beginner Notes

- The app uses Flask because it keeps the project small and easy to follow.
- SQLite stores the data in one local file, so there is no separate database server to set up.
- The templates folder holds the page layouts.
- The static folder holds the CSS for the visual design.
- `app.py` contains the routes, database setup, validation, and helper functions in one place so it is easier to learn from in version 1.
