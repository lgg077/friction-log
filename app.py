from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Flask, current_app, flash, g, redirect, render_template, request, url_for


BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
DATABASE_PATH = INSTANCE_DIR / "friction_log.db"

FREQUENCY_OPTIONS = [
    {"value": "rarely", "label": "Rarely", "score": 1},
    {"value": "monthly", "label": "Monthly", "score": 2},
    {"value": "weekly", "label": "Weekly", "score": 3},
    {"value": "several_times_week", "label": "Several times a week", "score": 4},
    {"value": "daily", "label": "Daily", "score": 5},
]

STATUS_OPTIONS = [
    "New",
    "Reviewing",
    "Planned",
    "In Progress",
    "Resolved",
]

FREQUENCY_LOOKUP = {option["value"]: option for option in FREQUENCY_OPTIONS}


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.update(
        SECRET_KEY="friction-log-dev-key",
        DATABASE=str(DATABASE_PATH),
    )

    if test_config:
        app.config.update(test_config)

    Path(app.config["DATABASE"]).resolve().parent.mkdir(parents=True, exist_ok=True)

    @app.before_request
    def before_request() -> None:
        init_db()

    @app.teardown_appcontext
    def close_db(_: BaseException | None) -> None:
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.context_processor
    def inject_shared_data() -> dict[str, Any]:
        return {
            "frequency_options": FREQUENCY_OPTIONS,
            "status_options": STATUS_OPTIONS,
        }

    @app.route("/")
    def dashboard() -> str:
        selected_department = request.args.get("department", "").strip()
        selected_status = request.args.get("status", "").strip()

        query = """
            SELECT
                id,
                title,
                department,
                description,
                business_impact,
                frequency_value,
                frequency_label,
                frequency_score,
                pain_score,
                estimated_hours_lost,
                current_workaround,
                ideal_outcome,
                status,
                date_created,
                ROUND(pain_score * frequency_score * estimated_hours_lost, 2) AS priority_score
            FROM frustrations
            WHERE (? = '' OR department = ?)
              AND (? = '' OR status = ?)
            ORDER BY priority_score DESC, date_created DESC
        """

        frustrations = query_db(
            query,
            (
                selected_department,
                selected_department,
                selected_status,
                selected_status,
            ),
        )

        departments = query_db(
            "SELECT DISTINCT department FROM frustrations ORDER BY department"
        )

        return render_template(
            "dashboard.html",
            frustrations=frustrations,
            departments=departments,
            selected_department=selected_department,
            selected_status=selected_status,
        )

    @app.route("/frustrations/new", methods=("GET", "POST"))
    def create_frustration() -> str:
        form_data = default_form_data()

        if request.method == "POST":
            form_data = form_from_request()
            errors = validate_form(form_data)

            if not errors:
                execute_db(
                    """
                    INSERT INTO frustrations (
                        title,
                        department,
                        description,
                        business_impact,
                        frequency_value,
                        frequency_label,
                        frequency_score,
                        pain_score,
                        estimated_hours_lost,
                        current_workaround,
                        ideal_outcome,
                        status,
                        date_created
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    frustration_values(form_data, datetime.now().strftime("%Y-%m-%d")),
                )
                flash("Frustration added.")
                return redirect(url_for("dashboard"))

            for error in errors:
                flash(error, "error")

        return render_template(
            "frustration_form.html",
            page_title="Add a Frustration",
            submit_label="Create entry",
            form_data=form_data,
        )

    @app.route("/frustrations/<int:frustration_id>/edit", methods=("GET", "POST"))
    def edit_frustration(frustration_id: int) -> str:
        frustration = get_frustration(frustration_id)

        if frustration is None:
            flash("That frustration entry could not be found.", "error")
            return redirect(url_for("dashboard"))

        form_data = row_to_form_data(frustration)

        if request.method == "POST":
            form_data = form_from_request()
            errors = validate_form(form_data)

            if not errors:
                execute_db(
                    """
                    UPDATE frustrations
                    SET
                        title = ?,
                        department = ?,
                        description = ?,
                        business_impact = ?,
                        frequency_value = ?,
                        frequency_label = ?,
                        frequency_score = ?,
                        pain_score = ?,
                        estimated_hours_lost = ?,
                        current_workaround = ?,
                        ideal_outcome = ?,
                        status = ?
                    WHERE id = ?
                    """,
                    frustration_values(form_data) + (frustration_id,),
                )
                flash("Frustration updated.")
                return redirect(url_for("dashboard"))

            for error in errors:
                flash(error, "error")

        return render_template(
            "frustration_form.html",
            page_title="Edit a Frustration",
            submit_label="Save changes",
            form_data=form_data,
        )

    @app.route("/frustrations/<int:frustration_id>/delete", methods=("GET", "POST"))
    def delete_frustration(frustration_id: int) -> str:
        frustration = get_frustration(frustration_id)

        if frustration is None:
            flash("That frustration entry could not be found.", "error")
            return redirect(url_for("dashboard"))

        if request.method == "POST":
            execute_db("DELETE FROM frustrations WHERE id = ?", (frustration_id,))
            flash("Frustration deleted.")
            return redirect(url_for("dashboard"))

        return render_template("delete_confirmation.html", frustration=frustration)

    return app


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
    return g.db


def init_db() -> None:
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS frustrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            department TEXT NOT NULL,
            description TEXT NOT NULL,
            business_impact TEXT NOT NULL,
            frequency_value TEXT NOT NULL,
            frequency_label TEXT NOT NULL,
            frequency_score INTEGER NOT NULL,
            pain_score INTEGER NOT NULL,
            estimated_hours_lost REAL NOT NULL,
            current_workaround TEXT NOT NULL,
            ideal_outcome TEXT NOT NULL,
            status TEXT NOT NULL,
            date_created TEXT NOT NULL
        )
        """
    )
    db.commit()


def query_db(query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    return get_db().execute(query, params).fetchall()


def execute_db(query: str, params: tuple[Any, ...]) -> None:
    db = get_db()
    db.execute(query, params)
    db.commit()


def get_frustration(frustration_id: int) -> sqlite3.Row | None:
    result = get_db().execute(
        """
        SELECT
            id,
            title,
            department,
            description,
            business_impact,
            frequency_value,
            frequency_label,
            frequency_score,
            pain_score,
            estimated_hours_lost,
            current_workaround,
            ideal_outcome,
            status,
            date_created,
            ROUND(pain_score * frequency_score * estimated_hours_lost, 2) AS priority_score
        FROM frustrations
        WHERE id = ?
        """,
        (frustration_id,),
    ).fetchone()
    return result


def default_form_data() -> dict[str, str]:
    return {
        "title": "",
        "department": "",
        "description": "",
        "business_impact": "",
        "frequency_value": "",
        "pain_score": "",
        "estimated_hours_lost": "",
        "current_workaround": "",
        "ideal_outcome": "",
        "status": "New",
    }


def row_to_form_data(frustration: sqlite3.Row) -> dict[str, str]:
    return {
        "title": frustration["title"],
        "department": frustration["department"],
        "description": frustration["description"],
        "business_impact": frustration["business_impact"],
        "frequency_value": frustration["frequency_value"],
        "pain_score": str(frustration["pain_score"]),
        "estimated_hours_lost": str(frustration["estimated_hours_lost"]),
        "current_workaround": frustration["current_workaround"],
        "ideal_outcome": frustration["ideal_outcome"],
        "status": frustration["status"],
    }


def form_from_request() -> dict[str, str]:
    fields = default_form_data().keys()
    return {field: request.form.get(field, "").strip() for field in fields}


def validate_form(form_data: dict[str, str]) -> list[str]:
    errors: list[str] = []

    for field, value in form_data.items():
        if not value:
            label = field.replace("_", " ").capitalize()
            errors.append(f"{label} is required.")

    frequency = FREQUENCY_LOOKUP.get(form_data["frequency_value"])
    if frequency is None:
        errors.append("Please choose a valid frequency.")

    try:
        pain_score = int(form_data["pain_score"])
        if pain_score < 1 or pain_score > 10:
            errors.append("Pain score must be between 1 and 10.")
    except ValueError:
        errors.append("Pain score must be a whole number between 1 and 10.")

    try:
        estimated_hours_lost = float(form_data["estimated_hours_lost"])
        if estimated_hours_lost < 0:
            errors.append("Estimated hours lost cannot be negative.")
    except ValueError:
        errors.append("Estimated hours lost must be a number.")

    if form_data["status"] not in STATUS_OPTIONS:
        errors.append("Please choose a valid status.")

    return unique_errors(errors)


def unique_errors(errors: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for error in errors:
        if error not in seen:
            unique.append(error)
            seen.add(error)
    return unique


def frustration_values(
    form_data: dict[str, str], date_created: str | None = None
) -> tuple[Any, ...]:
    frequency = FREQUENCY_LOOKUP[form_data["frequency_value"]]
    values: list[Any] = [
        form_data["title"],
        form_data["department"],
        form_data["description"],
        form_data["business_impact"],
        form_data["frequency_value"],
        frequency["label"],
        frequency["score"],
        int(form_data["pain_score"]),
        float(form_data["estimated_hours_lost"]),
        form_data["current_workaround"],
        form_data["ideal_outcome"],
        form_data["status"],
    ]
    if date_created is not None:
        values.append(date_created)
    return tuple(values)


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
