from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, parse, request as urllib_request

from flask import Flask, flash, redirect, render_template, request, url_for


BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
DATA_FILE_PATH = INSTANCE_DIR / "friction_log.json"
DEFAULT_SUPABASE_URL = "https://kdqmcufctsvjiyeuajpe.supabase.co"

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
        SECRET_KEY=os.environ.get("SECRET_KEY", "friction-log-dev-key"),
        DATA_FILE=str(DATA_FILE_PATH),
        STORAGE_BACKEND="file",
        SUPABASE_URL=os.environ.get("SUPABASE_URL", DEFAULT_SUPABASE_URL).strip()
        or DEFAULT_SUPABASE_URL,
        SUPABASE_SERVICE_ROLE_KEY=os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip(),
    )

    if test_config:
        app.config.update(test_config)

    app.config["STORAGE_BACKEND"] = (
        "supabase" if app.config["SUPABASE_SERVICE_ROLE_KEY"].strip() else "file"
    )

    if os.environ.get("VERCEL") and app.config["STORAGE_BACKEND"] != "supabase":
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY must be set in Vercel.")

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
                if using_supabase_storage():
                    create_frustration_in_supabase(form_data)
                else:
                    frustrations = load_frustrations()
                    frustrations.append(
                        build_local_record(
                            form_data=form_data,
                            frustration_id=next_frustration_id(frustrations),
                        )
                    )
                    save_frustrations_to_file(frustrations)
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
        frustration = get_frustration(frustration_id)

        if frustration is None:
            flash("That frustration entry could not be found.", "error")
            return redirect(url_for("dashboard"))

        form_data = row_to_form_data(frustration)

        if request.method == "POST":
            form_data = form_from_request()
            errors = validate_form(form_data)

            if not errors:
                if using_supabase_storage():
                    update_frustration_in_supabase(frustration_id, form_data)
                else:
                    frustrations = load_frustrations()
                    updated_record = build_local_record(
                        form_data=form_data,
                        frustration_id=frustration_id,
                        created_at=frustration["created_at"],
                    )
                    replace_frustration(frustrations, frustration_id, updated_record)
                    save_frustrations_to_file(frustrations)
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
        frustration = get_frustration(frustration_id)

        if frustration is None:
            flash("That frustration entry could not be found.", "error")
            return redirect(url_for("dashboard"))

        if request.method == "POST":
            if using_supabase_storage():
                delete_frustration_from_supabase(frustration_id)
            else:
                frustrations = [
                    entry for entry in load_frustrations() if entry["id"] != frustration_id
                ]
                save_frustrations_to_file(frustrations)
            flash("Frustration deleted.")
            return redirect(url_for("dashboard"))

        return render_template("delete_confirmation.html", frustration=frustration)

    return app


def using_supabase_storage() -> bool:
    from flask import current_app

    return current_app.config["STORAGE_BACKEND"] == "supabase"


def load_frustrations() -> list[dict[str, Any]]:
    if using_supabase_storage():
        return load_frustrations_from_supabase()
    return load_frustrations_from_file()


def get_frustration(frustration_id: int) -> dict[str, Any] | None:
    frustrations = load_frustrations()
    for frustration in frustrations:
        if int(frustration["id"]) == frustration_id:
            return frustration
    return None


def load_frustrations_from_file() -> list[dict[str, Any]]:
    from flask import current_app

    data_file = Path(current_app.config["DATA_FILE"])
    if not data_file.exists():
        return []
    try:
        content = json.loads(data_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return [normalize_frustration_record(entry) for entry in content]


def save_frustrations_to_file(frustrations: list[dict[str, Any]]) -> None:
    from flask import current_app

    data_file = Path(current_app.config["DATA_FILE"])
    stored = [strip_computed_fields(frustration) for frustration in frustrations]
    data_file.write_text(json.dumps(stored, indent=2), encoding="utf-8")


def load_frustrations_from_supabase() -> list[dict[str, Any]]:
    rows = supabase_request_json(
        method="GET",
        path="/rest/v1/frustrations",
        query_params={
            "select": "*",
            "order": "created_at.desc",
        },
    )
    return [normalize_frustration_record(row) for row in rows]


def create_frustration_in_supabase(form_data: dict[str, str]) -> None:
    supabase_request_json(
        method="POST",
        path="/rest/v1/frustrations",
        json_body=build_supabase_payload(form_data),
        extra_headers={"Prefer": "return=minimal"},
    )


def update_frustration_in_supabase(frustration_id: int, form_data: dict[str, str]) -> None:
    payload = build_supabase_payload(form_data)
    payload["updated_at"] = utc_now_iso()
    supabase_request_json(
        method="PATCH",
        path="/rest/v1/frustrations",
        query_params={"id": f"eq.{frustration_id}"},
        json_body=payload,
        extra_headers={"Prefer": "return=minimal"},
    )


def delete_frustration_from_supabase(frustration_id: int) -> None:
    supabase_request_json(
        method="DELETE",
        path="/rest/v1/frustrations",
        query_params={"id": f"eq.{frustration_id}"},
        extra_headers={"Prefer": "return=minimal"},
    )


def build_supabase_payload(form_data: dict[str, str]) -> dict[str, Any]:
    frequency = FREQUENCY_LOOKUP[form_data["frequency_value"]]
    return {
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
    }


def supabase_request_json(
    method: str,
    path: str,
    query_params: dict[str, str] | None = None,
    json_body: Any | None = None,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    from flask import current_app

    base_url = current_app.config["SUPABASE_URL"].rstrip("/")
    service_role_key = current_app.config["SUPABASE_SERVICE_ROLE_KEY"]
    if not service_role_key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is required for Supabase storage.")

    url = f"{base_url}{path}"
    if query_params:
        url = f"{url}?{parse.urlencode(query_params)}"

    headers = {
        "apikey": service_role_key,
        "Authorization": f"Bearer {service_role_key}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    payload = None
    if json_body is not None:
        payload = json.dumps(json_body).encode("utf-8")

    request_obj = urllib_request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib_request.urlopen(request_obj, timeout=20) as response:
            body = response.read().decode("utf-8")
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(
            f"Supabase request failed: {exc.code} {details}".strip()
        ) from exc

    if not body:
        return []
    return json.loads(body)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_frustration_record(frustration: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(frustration)
    if "created_at" not in normalized:
        normalized["created_at"] = utc_now_iso()
    if "updated_at" not in normalized:
        normalized["updated_at"] = normalized["created_at"]
    normalized["estimated_hours_lost"] = float(normalized["estimated_hours_lost"])
    normalized["pain_score"] = int(normalized["pain_score"])
    normalized["frequency_score"] = int(normalized["frequency_score"])
    normalized["date_created"] = normalized["created_at"][:10]
    normalized["priority_score"] = priority_score(normalized)
    return normalized


def strip_computed_fields(frustration: dict[str, Any]) -> dict[str, Any]:
    stored = dict(frustration)
    stored.pop("priority_score", None)
    stored.pop("date_created", None)
    return stored


def build_local_record(
    form_data: dict[str, str], frustration_id: int, created_at: str | None = None
) -> dict[str, Any]:
    payload = build_supabase_payload(form_data)
    payload["id"] = frustration_id
    payload["created_at"] = created_at or utc_now_iso()
    payload["updated_at"] = utc_now_iso()
    return normalize_frustration_record(payload)


def priority_score(frustration: dict[str, Any]) -> float:
    return round(
        float(frustration["pain_score"])
        * float(frustration["frequency_score"])
        * float(frustration["estimated_hours_lost"]),
        2,
    )


def next_frustration_id(frustrations: list[dict[str, Any]]) -> int:
    if not frustrations:
        return 1
    return max(int(frustration["id"]) for frustration in frustrations) + 1


def replace_frustration(
    frustrations: list[dict[str, Any]],
    frustration_id: int,
    updated_record: dict[str, Any],
) -> None:
    for index, frustration in enumerate(frustrations):
        if int(frustration["id"]) == frustration_id:
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
