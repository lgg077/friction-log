from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error, parse, request as urllib_request

from flask import Flask, flash, redirect, render_template, request, url_for


BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
DATA_FILE_PATH = INSTANCE_DIR / "friction_log.json"
BLOB_API_URL = "https://vercel.com/api/blob"
BLOB_API_VERSION = "12"

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

    blob_token = os.environ.get("BLOB_READ_WRITE_TOKEN", "").strip()
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "friction-log-dev-key"),
        STORAGE_BACKEND="blob" if blob_token else "file",
        DATA_FILE=str(DATA_FILE_PATH),
        BLOB_TOKEN=blob_token,
        BLOB_ACCESS=os.environ.get("BLOB_ACCESS", "private").strip() or "private",
        BLOB_PATHNAME=os.environ.get(
            "BLOB_PATHNAME", "friction-log/frustrations.json"
        ).strip()
        or "friction-log/frustrations.json",
    )

    if test_config:
        app.config.update(test_config)

    if app.config["STORAGE_BACKEND"] == "file":
        Path(app.config["DATA_FILE"]).resolve().parent.mkdir(parents=True, exist_ok=True)

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

        frustrations = load_frustrations()
        frustrations = [
            frustration
            for frustration in frustrations
            if (not selected_department or frustration["department"] == selected_department)
            and (not selected_status or frustration["status"] == selected_status)
        ]
        frustrations.sort(
            key=lambda frustration: (
                -priority_score(frustration),
                frustration["date_created"],
            )
        )
        departments = sorted(
            {frustration["department"] for frustration in load_frustrations()}
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
                frustrations = load_frustrations()
                frustrations.append(
                    build_frustration_record(
                        form_data=form_data,
                        frustration_id=next_frustration_id(frustrations),
                        date_created=datetime.now().strftime("%Y-%m-%d"),
                    )
                )
                save_frustrations(frustrations)
                flash("Frustration added.")
                return redirect(url_for("dashboard"))

            for error_message in errors:
                flash(error_message, "error")

        return render_template(
            "frustration_form.html",
            page_title="Add a Frustration",
            submit_label="Create entry",
            form_data=form_data,
        )

    @app.route("/frustrations/<int:frustration_id>/edit", methods=("GET", "POST"))
    def edit_frustration(frustration_id: int) -> str:
        frustrations = load_frustrations()
        frustration = find_frustration(frustrations, frustration_id)

        if frustration is None:
            flash("That frustration entry could not be found.", "error")
            return redirect(url_for("dashboard"))

        form_data = row_to_form_data(frustration)

        if request.method == "POST":
            form_data = form_from_request()
            errors = validate_form(form_data)

            if not errors:
                updated_record = build_frustration_record(
                    form_data=form_data,
                    frustration_id=frustration_id,
                    date_created=frustration["date_created"],
                )
                replace_frustration(frustrations, frustration_id, updated_record)
                save_frustrations(frustrations)
                flash("Frustration updated.")
                return redirect(url_for("dashboard"))

            for error_message in errors:
                flash(error_message, "error")

        return render_template(
            "frustration_form.html",
            page_title="Edit a Frustration",
            submit_label="Save changes",
            form_data=form_data,
        )

    @app.route("/frustrations/<int:frustration_id>/delete", methods=("GET", "POST"))
    def delete_frustration(frustration_id: int) -> str:
        frustrations = load_frustrations()
        frustration = find_frustration(frustrations, frustration_id)

        if frustration is None:
            flash("That frustration entry could not be found.", "error")
            return redirect(url_for("dashboard"))

        if request.method == "POST":
            frustrations = [
                entry for entry in frustrations if entry["id"] != frustration_id
            ]
            save_frustrations(frustrations)
            flash("Frustration deleted.")
            return redirect(url_for("dashboard"))

        return render_template("delete_confirmation.html", frustration=frustration)

    return app


def load_frustrations() -> list[dict[str, Any]]:
    if using_blob_storage():
        return load_frustrations_from_blob()
    return load_frustrations_from_file()


def save_frustrations(frustrations: list[dict[str, Any]]) -> None:
    normalized = [with_priority_score(frustration) for frustration in frustrations]
    if using_blob_storage():
        save_frustrations_to_blob(normalized)
    else:
        save_frustrations_to_file(normalized)


def using_blob_storage() -> bool:
    from flask import current_app

    return current_app.config["STORAGE_BACKEND"] == "blob"


def load_frustrations_from_file() -> list[dict[str, Any]]:
    from flask import current_app

    data_file = Path(current_app.config["DATA_FILE"])
    if not data_file.exists():
        return []
    try:
        content = json.loads(data_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return [with_priority_score(entry) for entry in content]


def save_frustrations_to_file(frustrations: list[dict[str, Any]]) -> None:
    from flask import current_app

    data_file = Path(current_app.config["DATA_FILE"])
    data_file.write_text(
        json.dumps(frustrations, indent=2),
        encoding="utf-8",
    )


def load_frustrations_from_blob() -> list[dict[str, Any]]:
    blob_url = build_blob_url()
    headers = {"Authorization": f"Bearer {blob_token()}"}
    request_obj = urllib_request.Request(blob_url, headers=headers, method="GET")
    try:
        with urllib_request.urlopen(request_obj, timeout=15) as response:
            body = response.read().decode("utf-8")
    except error.HTTPError as exc:
        if exc.code == 404:
            return []
        raise RuntimeError(f"Could not read Blob data: {exc.code}") from exc
    content = json.loads(body or "[]")
    return [with_priority_score(entry) for entry in content]


def save_frustrations_to_blob(frustrations: list[dict[str, Any]]) -> None:
    pathname = blob_pathname()
    query = parse.urlencode({"pathname": pathname})
    payload = json.dumps(frustrations).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {blob_token()}",
        "Content-Type": "application/json",
        "x-api-version": BLOB_API_VERSION,
        "x-vercel-blob-store-id": blob_store_id(),
        "x-vercel-blob-access": blob_access(),
        "x-content-length": str(len(payload)),
        "x-content-type": "application/json",
        "x-add-random-suffix": "0",
        "x-allow-overwrite": "1",
    }
    request_obj = urllib_request.Request(
        f"{BLOB_API_URL}/?{query}",
        data=payload,
        headers=headers,
        method="PUT",
    )
    try:
        with urllib_request.urlopen(request_obj, timeout=20) as response:
            response.read()
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(
            f"Could not write Blob data: {exc.code} {details}".strip()
        ) from exc


def blob_token() -> str:
    from flask import current_app

    token = current_app.config["BLOB_TOKEN"]
    if not token:
        raise RuntimeError("BLOB_READ_WRITE_TOKEN is required for Blob storage.")
    return token


def blob_access() -> str:
    from flask import current_app

    access = current_app.config["BLOB_ACCESS"]
    if access not in {"public", "private"}:
        raise RuntimeError("BLOB_ACCESS must be either 'public' or 'private'.")
    return access


def blob_pathname() -> str:
    from flask import current_app

    return current_app.config["BLOB_PATHNAME"]


def blob_store_id() -> str:
    token = blob_token()
    parts = token.split("_")
    if len(parts) < 4 or not parts[3]:
        raise RuntimeError("Could not extract the Blob store ID from the read/write token.")
    return parts[3]


def build_blob_url() -> str:
    return (
        f"https://{blob_store_id()}.{blob_access()}.blob.vercel-storage.com/"
        f"{blob_pathname()}"
    )


def priority_score(frustration: dict[str, Any]) -> float:
    return round(
        float(frustration["pain_score"])
        * float(frustration["frequency_score"])
        * float(frustration["estimated_hours_lost"]),
        2,
    )


def with_priority_score(frustration: dict[str, Any]) -> dict[str, Any]:
    frustration_copy = dict(frustration)
    frustration_copy["priority_score"] = priority_score(frustration_copy)
    return frustration_copy


def next_frustration_id(frustrations: list[dict[str, Any]]) -> int:
    if not frustrations:
        return 1
    return max(int(frustration["id"]) for frustration in frustrations) + 1


def build_frustration_record(
    form_data: dict[str, str], frustration_id: int, date_created: str
) -> dict[str, Any]:
    frequency = FREQUENCY_LOOKUP[form_data["frequency_value"]]
    return with_priority_score(
        {
            "id": frustration_id,
            "title": form_data["title"],
            "department": form_data["department"],
            "description": form_data["description"],
            "business_impact": form_data["business_impact"],
            "frequency_value": form_data["frequency_value"],
            "frequency_label": frequency["label"],
            "frequency_score": frequency["score"],
            "pain_score": int(form_data["pain_score"]),
            "estimated_hours_lost": float(form_data["estimated_hours_lost"]),
            "current_workaround": form_data["current_workaround"],
            "ideal_outcome": form_data["ideal_outcome"],
            "status": form_data["status"],
            "date_created": date_created,
        }
    )


def find_frustration(
    frustrations: list[dict[str, Any]], frustration_id: int
) -> dict[str, Any] | None:
    for frustration in frustrations:
        if frustration["id"] == frustration_id:
            return with_priority_score(frustration)
    return None


def replace_frustration(
    frustrations: list[dict[str, Any]],
    frustration_id: int,
    updated_record: dict[str, Any],
) -> None:
    for index, frustration in enumerate(frustrations):
        if frustration["id"] == frustration_id:
            frustrations[index] = updated_record
            return


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


def row_to_form_data(frustration: dict[str, Any]) -> dict[str, str]:
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
    for error_message in errors:
        if error_message not in seen:
            unique.append(error_message)
            seen.add(error_message)
    return unique


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
