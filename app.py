from flask import Flask, jsonify, request
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.ext.flask.middleware import XRayMiddleware
import logging
from aws_xray_sdk.core import patch_all
import json
import uuid
import time
import random
import os
import boto3

# ========== CONFIG SECTION ==========
# Values can be overridden by environment variables
APP_NAME = os.environ.get('APP_NAME', "FlaskXRayTemplate")
LOG_LEVEL = logging.getLevelName(os.environ.get('LOG_LEVEL', 'INFO'))
CW_LOG_GROUP = os.environ.get('CW_LOG_GROUP', "/flask/xray-demo")
ENABLE_X_RAY = os.environ.get('ENABLE_X_RAY', 'true').lower() == 'true'
ENABLE_CW_LOGS = os.environ.get('ENABLE_CW_LOGS', 'true').lower() == 'true'
STAGE = os.environ.get('STAGE', 'dev')
# ===================================

# Configure logging
logger = logging.getLogger(APP_NAME)
logger.setLevel(LOG_LEVEL)

# Create a custom formatter that includes timestamp and request metadata
class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "message": record.getMessage(),
            "app_name": APP_NAME,
            "stage": STAGE,
        }
        
        # Add request_id if available
        if hasattr(record, "request_id"):
            log_record["request_id"] = record.request_id
        
        # Add any custom metadata fields
        for attr in dir(record):
            if attr.startswith('metadata_') and not callable(getattr(record, attr)):
                key = attr.replace('metadata_', '')
                log_record[key] = getattr(record, attr)
                
        return json.dumps(log_record)

# Add console handler for local development
console_handler = logging.StreamHandler()
console_handler.setFormatter(JsonFormatter())
logger.addHandler(console_handler)

# Add CloudWatch Logs handler if enabled
if ENABLE_CW_LOGS:
    try:
        import watchtower
        # Create log stream name with app name, stage and uuid
        log_stream_name = f"{APP_NAME}-{STAGE}-{str(uuid.uuid4())[:8]}"
        
        # Initialize CloudWatch Logs handler
        cw_handler = watchtower.CloudWatchLogHandler(
            log_group=CW_LOG_GROUP,
            stream_name=log_stream_name,
            boto3_client=boto3.client('logs')
        )
        cw_handler.setFormatter(JsonFormatter())
        logger.addHandler(cw_handler)
        logger.info(f"CloudWatch Logs enabled with log group {CW_LOG_GROUP}, stream {log_stream_name}")
    except Exception as e:
        logger.warning(f"Failed to initialize CloudWatch Logs: {str(e)}")

# Initialize Flask app
app = Flask(__name__)

# Configure X-Ray if enabled
if ENABLE_X_RAY:
    try:
        xray_recorder.configure(
            service=APP_NAME,
            context_missing='LOG_ERROR'
        )
        XRayMiddleware(app, xray_recorder)
        patch_all()  # Patch all supported libraries for X-Ray
        logger.info("AWS X-Ray integration enabled")
    except Exception as e:
        logger.warning(f"Failed to initialize X-Ray: {str(e)}")

# ========== MIDDLEWARE SECTION ==========

# Request middleware to add request_id and start time
@app.before_request
def before_request():
    # Generate or retrieve request ID
    if request.headers.get('X-Request-ID'):
        request.request_id = request.headers.get('X-Request-ID')
    else:
        request.request_id = str(uuid.uuid4())
    
    # Record start time for duration calculation
    request.start_time = time.time()
    
    # Log incoming request
    logger.info(f"Received request: {request.method} {request.path}",
                extra={
                    'request_id': request.request_id,
                    'metadata_method': request.method,
                    'metadata_path': request.path,
                    'metadata_ip': request.remote_addr
                })

# Logging middleware
@app.after_request
def after_request(response):
    # Calculate request duration
    duration_ms = 0
    if hasattr(request, 'start_time'):
        duration_ms = int((time.time() - request.start_time) * 1000)
    
    # Add response headers
    response.headers['X-Request-ID'] = getattr(request, 'request_id', 'unknown')
    
    # Log request completion
    logger.info(
        f"Request completed: {request.method} {request.path}",
        extra={
            'request_id': getattr(request, 'request_id', 'unknown'),
            'metadata_method': request.method,
            'metadata_path': request.path,
            'metadata_status_code': response.status_code,
            'metadata_duration_ms': duration_ms,
            'metadata_ip': request.remote_addr
        }
    )
    return response

# Error handling
@app.errorhandler(Exception)
def handle_exception(e):
    logger.exception(f"Unhandled exception: {str(e)}",
                     extra={'request_id': getattr(request, 'request_id', 'unknown')})
    
    return jsonify({
        "error": str(e),
        "request_id": getattr(request, "request_id", "unknown")
    }), 500

# ========== UTILITY FUNCTIONS ==========

# Simulate database operation with X-Ray subsegment
def simulate_db_operation(operation_name, failure_rate=0.05):
    """
    Generic database operation simulator with customizable failure rate
    
    Args:
        operation_name (str): Name of the database operation
        failure_rate (float): Probability of failure (0.0 to 1.0)
        
    Returns:
        dict: Result of the operation
    """
    if not ENABLE_X_RAY:
        # If X-Ray is disabled, skip subsegment creation
        time.sleep(random.uniform(0.05, 0.2))
        if random.random() < failure_rate:
            logger.error(f"Database operation '{operation_name}' failed")
            return {"status": "error", "operation": operation_name}
        logger.info(f"Database operation '{operation_name}' completed")
        return {"status": "success", "operation": operation_name}
    
    with xray_recorder.in_subsegment(f'database_{operation_name}'):
        # Simulate DB latency
        time.sleep(random.uniform(0.05, 0.2))
        
        # Simulate failures based on failure_rate
        if random.random() < failure_rate:
            logger.error(f"Database operation '{operation_name}' failed")
            xray_recorder.current_subsegment().add_exception(
                Exception(f"DB operation {operation_name} failure"),
                stack=None
            )
            return {"status": "error", "operation": operation_name}
            
        logger.info(f"Database operation '{operation_name}' completed")
        return {"status": "success", "operation": operation_name}

# Simulate external API call with X-Ray subsegment
def simulate_external_api_call(api_name, failure_rate=0.1):
    """
    Generic external API call simulator with customizable failure rate
    
    Args:
        api_name (str): Name of the external API
        failure_rate (float): Probability of failure (0.0 to 1.0)
        
    Returns:
        dict: Result of the API call
    """
    if not ENABLE_X_RAY:
        # If X-Ray is disabled, skip subsegment creation
        time.sleep(random.uniform(0.1, 0.3))
        if random.random() < failure_rate:
            logger.error(f"API call to '{api_name}' failed")
            return {"status": "error", "api": api_name}
        logger.info(f"API call to '{api_name}' completed")
        return {"status": "success", "api": api_name}
    
    with xray_recorder.in_subsegment(f'external_api_{api_name}'):
        # Simulate API latency
        time.sleep(random.uniform(0.1, 0.3))
        
        # Random failure simulation based on failure_rate
        if random.random() < failure_rate:
            logger.error(f"API call to '{api_name}' failed")
            xray_recorder.current_subsegment().add_exception(
                Exception(f"API {api_name} failure"),
                stack=None
            )
            return {"status": "error", "api": api_name}
            
        logger.info(f"API call to '{api_name}' completed")
        return {"status": "success", "api": api_name}

# Add X-Ray annotation if enabled
def add_annotation(key, value):
    """Add X-Ray annotation safely"""
    if ENABLE_X_RAY and xray_recorder.current_segment():
        try:
            xray_recorder.current_segment().put_annotation(key, value)
        except Exception as e:
            logger.warning(f"Failed to add X-Ray annotation: {str(e)}")

# Add X-Ray metadata if enabled
def add_metadata(namespace, key, value):
    """Add X-Ray metadata safely"""
    if ENABLE_X_RAY and xray_recorder.current_segment():
        try:
            xray_recorder.current_segment().put_metadata(key, value, namespace)
        except Exception as e:
            logger.warning(f"Failed to add X-Ray metadata: {str(e)}")

# ========== ROUTE HANDLERS ==========

# Root endpoint
@app.route('/')
def index():
    try:
        logger.info("Processing root endpoint request", 
                    extra={'request_id': getattr(request, 'request_id', 'unknown')})
        
        return jsonify({
            "service": APP_NAME,
            "status": "healthy",
            "version": "1.0.0",
            "stage": STAGE,
            "request_id": getattr(request, "request_id", "unknown")
        })
    except Exception as e:
        logger.exception(f"Error in root endpoint: {str(e)}",
                         extra={'request_id': getattr(request, 'request_id', 'unknown')})
        raise

# Example resource endpoint
@app.route('/resources/<resource_id>')
def get_resource(resource_id):
    try:
        logger.info(f"Fetching resource data", 
                    extra={
                        'request_id': getattr(request, 'request_id', 'unknown'),
                        'metadata_resource_id': resource_id
                    })
        
        # Add X-Ray annotation
        add_annotation('resource_id', resource_id)
        
        # Simulate database query
        db_result = simulate_db_operation("get_resource")
        
        return jsonify({
            "resource_id": resource_id,
            "name": f"Resource {resource_id}",
            "status": db_result["status"],
            "request_id": getattr(request, "request_id", "unknown")
        })
    except Exception as e:
        logger.exception(f"Error fetching resource: {str(e)}",
                         extra={'request_id': getattr(request, 'request_id', 'unknown')})
        raise

# Example nested resource endpoint with multiple operations
@app.route('/resources/<resource_id>/items')
def get_resource_items(resource_id):
    try:
        logger.info(f"Fetching items for resource", 
                    extra={
                        'request_id': getattr(request, 'request_id', 'unknown'),
                        'metadata_resource_id': resource_id
                    })
        
        # Add X-Ray annotations and metadata
        add_annotation('resource_id', resource_id)
        add_metadata('request', 'query_params', dict(request.args))
        
        # Simulate multiple operations
        db_result = simulate_db_operation("get_items")
        api_result = simulate_external_api_call("validation_service")
        
        # Create sample response data
        items = [
            {"item_id": f"{resource_id}-1", "status": "active"},
            {"item_id": f"{resource_id}-2", "status": "pending"}
        ]
        
        return jsonify({
            "resource_id": resource_id,
            "items": items,
            "db_status": db_result["status"],
            "api_status": api_result["status"],
            "request_id": getattr(request, "request_id", "unknown")
        })
    except Exception as e:
        logger.exception(f"Error fetching resource items: {str(e)}",
                         extra={'request_id': getattr(request, 'request_id', 'unknown')})
        raise

# Error endpoint to demonstrate error handling
@app.route('/error')
def error_endpoint():
    try:
        logger.info("Processing error endpoint request", 
                    extra={'request_id': getattr(request, 'request_id', 'unknown')})
        
        # Simulate error
        raise Exception("This is a test error")
    except Exception as e:
        logger.exception(f"Error occurred: {str(e)}", 
                        extra={'request_id': getattr(request, 'request_id', 'unknown')})
        
        # Add X-Ray annotation
        add_annotation('error', 'true')
        
        return jsonify({
            "error": str(e),
            "request_id": getattr(request, "request_id", "unknown")
        }), 500

# Health check endpoint
@app.route('/health')
def health():
    return jsonify({"status": "healthy"})

# ========== RUN APPLICATION ==========

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=os.environ.get('DEBUG', 'False').lower() == 'true', 
            host='0.0.0.0', 
            port=port)