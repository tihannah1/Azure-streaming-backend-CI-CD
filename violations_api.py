import json
from datetime import datetime, timedelta
from azure.cosmos.exceptions import CosmosHttpResponseError


def _parse_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_violations(req, violations_container):
    """Retrieve violations from a Cosmos DB container for moderator review.

    Args:
        req: An object exposing query parameters via req.params (typical Azure HttpRequest).
        violations_container: Cosmos DB container instance with a query_items method.

    Returns:
        A tuple (response_dict, status_code). response_dict will contain keys:
            - violations: list of violation documents
            - count: number of returned violations
            - applied_filters: dict of filters used
    """
    try:
        params = getattr(req, 'params', {}) or {}

        category = params.get('category')
        status = params.get('status')
        days = _parse_int(params.get('days'), 7)
        limit = _parse_int(params.get('limit'), 50)

        # If no filters provided, default to pending violations
        if not any([category, status, params.get('days'), params.get('limit')]):
            status = 'pending'

        min_ts = (datetime.utcnow() - timedelta(days=days)).isoformat() + 'Z'

        sql = 'SELECT * FROM c'
        where_clauses = []
        sql_params = []

        # timestamp filter
        if days is not None:
            where_clauses.append('c.timestamp >= @min_ts')
            sql_params.append({'name': '@min_ts', 'value': min_ts})

        if category:
            where_clauses.append('c.category = @category')
            sql_params.append({'name': '@category', 'value': category})

        if status:
            where_clauses.append('c.status = @status')
            sql_params.append({'name': '@status', 'value': status})

        if where_clauses:
            sql += ' WHERE ' + ' AND '.join(where_clauses)

        sql += ' ORDER BY c.timestamp DESC'

        items = list(violations_container.query_items(
            query=sql,
            parameters=sql_params,
            enable_cross_partition_query=True
        ))

        # enforce limit
        items = items[:limit]

        resp = {
            'violations': items,
            'count': len(items),
            'applied_filters': {
                'category': category,
                'status': status,
                'days': days,
                'limit': limit,
            }
        }

        return resp, 200

    except CosmosHttpResponseError as e:
        return {'error': 'Cosmos DB error', 'details': str(e)}, 500
    except Exception as e:
        return {'error': 'Internal server error', 'details': str(e)}, 500


def review_violation(req, violations_container):
    """Review a flagged violation by approving or rejecting it.

    Args:
        req: An object exposing route params via req.route_params and JSON body via req.get_json().
        violations_container: Cosmos DB container instance with query_items and upsert_item methods.

    Returns:
        A tuple (response_dict, status_code).
    """
    try:
        route_params = getattr(req, 'route_params', {}) or {}
        violation_id = route_params.get('id') or route_params.get('violationId')

        try:
            if hasattr(req, 'get_json'):
                body = req.get_json() or {}
            else:
                body = json.loads(req.get_body().decode('utf-8') or '{}')
        except Exception:
            return {'error': 'Invalid JSON body'}, 400

        action = body.get('action')
        moderator_id = body.get('moderatorId')
        notes = body.get('notes')

        if not violation_id:
            return {'error': 'Missing violation ID'}, 400
        if action not in ('approve', 'reject'):
            return {'error': 'Invalid action', 'details': 'action must be approve or reject'}, 400
        if not moderator_id:
            return {'error': 'Missing moderatorId'}, 400

        sql = 'SELECT * FROM c WHERE c.id = @id'
        sql_params = [{'name': '@id', 'value': violation_id}]
        items = list(violations_container.query_items(
            query=sql,
            parameters=sql_params,
            enable_cross_partition_query=True
        ))

        if not items:
            return {'error': 'Violation not found'}, 404

        violation = items[0]
        violation['reviewStatus'] = 'approved' if action == 'approve' else 'rejected'
        violation['reviewedBy'] = moderator_id
        violation['reviewedAt'] = datetime.utcnow().isoformat() + 'Z'
        violation['reviewNotes'] = notes if notes is not None else ''

        if action == 'reject':
            violation['falsePositive'] = True

        updated_violation = violations_container.upsert_item(violation)

        return updated_violation, 200

    except CosmosHttpResponseError as e:
        return {'error': 'Cosmos DB error', 'details': str(e)}, 500
    except Exception as e:
        return {'error': 'Internal server error', 'details': str(e)}, 500


def get_violations_container(cosmos_client, database_name, container_name):
    """Retrieve the Cosmos DB container used to store violations.

    Args:
        cosmos_client: An initialized CosmosClient instance.
        database_name: Name of the Cosmos DB database.
        container_name: Name of the container within the database.

    Returns:
        A Cosmos DB container proxy instance.
    """
    database = cosmos_client.get_database_client(database_name)
    return database.get_container_client(container_name)