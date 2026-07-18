from azure.cosmos import CosmosClient

import json
import os
import uuid
from datetime import datetime, timezone
import logging

import azure.functions as func
from google import genai
from moderation import check_message
from violations_api import get_violations, review_violation, get_violations_container

cosmos_conn = os.getenv("COSMOS_CONNECTION_STRING")

client_db = CosmosClient.from_connection_string(cosmos_conn)
database = client_db.get_database_client("streamingdb")
container = database.get_container_client("messages")
violations_container = database.get_container_client("violations")

# Create the Function App
app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# In-memory message store
messages = []

# Gemini client
api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key)


@app.function_name(name="PostMessage")
@app.route(route="message", methods=["POST"])
def post_message(req: func.HttpRequest) -> func.HttpResponse:
    try:
        try:
            body = req.get_json()
        except ValueError:
            return func.HttpResponse(
                json.dumps({"error": "Invalid JSON payload"}),
                status_code=400,
                mimetype="application/json",
            )

        content = body.get("content")
        username = body.get("username", "Anonymous")

        if not isinstance(content, str) or not content.strip():
            return func.HttpResponse(
                json.dumps(
                    {"error": "'content' is required and must be a non-empty string"}
                ),
                status_code=400,
                mimetype="application/json",
            )

        # Check message moderation
        result = check_message(content)
        logging.info(f"Moderation result: {result}")

        if not result["is_allowed"]:
            violation_record = {
                "id": str(uuid.uuid4()),
                "streamId": "default",
                "content": content,
                "username": username,
                "category": result["category"],
                "confidence": result["confidence"],
                "reason": result["reason"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            violations_container.create_item(body=violation_record)

            return func.HttpResponse(
                json.dumps(
                    {
                        "error": "Message blocked by moderation.",
                        "category": result["category"],
                        "confidence": result["confidence"],
                        "reason": result["reason"],
                    }
                ),
                status_code=403,
                mimetype="application/json",
            )

        # Save user's message
        user_message = {
            "id": str(uuid.uuid4()),
            "streamId": "default",
            "content": content,
            "username": username,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        container.create_item(body=user_message)

        # Ask Gemini
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=content,
        )

        # Save Gemini's response
        ai_message = {
            "id": str(uuid.uuid4()),
            "streamId": "default",
            "content": response.text,
            "username": "Gemini",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        container.create_item(body=ai_message)

        return func.HttpResponse(
            json.dumps(
                {
                    "user": user_message,
                    "assistant": ai_message,
                }
            ),
            status_code=201,
            mimetype="application/json",
        )

    except Exception as e:
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json",
        )


@app.function_name(name="GetMessages")
@app.route(route="messages", methods=["GET"])
def get_messages(req: func.HttpRequest) -> func.HttpResponse:
    try:
        items = list(container.read_all_items())

        return func.HttpResponse(
            json.dumps(items),
            status_code=200,
            mimetype="application/json",
        )

    except Exception as e:
        logging.error(str(e))
        return func.HttpResponse(
            "Error retrieving messages",
            status_code=500,
        )


@app.function_name(name="GetViolations")
@app.route(route="violations", methods=["GET"])
def get_violations_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    try:

        items = get_violations(violations_container)

        return func.HttpResponse(
            json.dumps(items),
            status_code=200,
            mimetype="application/json",
        )

    except Exception as e:
        logging.error(str(e))
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json",
        )


@app.function_name(name="ReviewViolation")
@app.route(route="violations/{id}", methods=["PATCH"])
def review_violation_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    try:
        violation_id = req.route_params.get("id")
        if not violation_id:
            return func.HttpResponse(
                json.dumps({"error": "Missing violation id in route"}),
                status_code=400,
                mimetype="application/json",
            )

        try:
            body = req.get_json()
        except ValueError:
            body = None

        result = review_violation(violations_container, violation_id, body)

        return func.HttpResponse(
            json.dumps(result),
            status_code=200,
            mimetype="application/json",
        )

    except Exception as e:
        logging.error(str(e))
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json",
        )


@app.function_name(name="Health")
@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps({"status": "Healthy"}),
        status_code=201,
        mimetype="application/json",
    )