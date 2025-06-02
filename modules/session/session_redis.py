import redis
import json
import time
import os
import uuid
from flask import request, make_response, jsonify



# Redis connection
redis_host = os.environ.get('REDIS_HOST', '')
redis_port = os.environ.get('REDIS_PORT', 6379)
redis_client = redis.Redis(host=redis_host, port=redis_port, db=0)
SESSION_EXPIRY = 86400

def check_auth():
    """Check if the current user is authenticated"""
    session_id = request.cookies.get('session_id')
    if not session_id:
        return False
    
    # Get session data from Redis
    session_data = redis_client.get(f"session:{session_id}")
    if not session_data:
        return False
    
    # Parse session data
    session = json.loads(session_data)
    
    # Check expiration
    if session.get('expires', 0) > time.time():
        # Update expiry time on access
        session['expires'] = time.time() + SESSION_EXPIRY
        redis_client.setex(
            f"session:{session_id}",
            SESSION_EXPIRY,
            json.dumps(session)
        )
        return True
    
    return False

def setup_login_routes(app, ADMIN_PASSWORD):
    @app.route('/login', methods=['POST'])
    def login():
        password = request.form.get('password')
        if password == ADMIN_PASSWORD:
            # Create a new session
            session_id = str(uuid.uuid4())
            
            # Session data to store
            session_data = {
                'created': time.time(),
                'expires': time.time() + SESSION_EXPIRY
            }
            
            # Store session in Redis
            redis_client.setex(
                f"session:{session_id}",  # Key
                SESSION_EXPIRY,           # Expiration in seconds
                json.dumps(session_data)  # Value (serialized as JSON)
            )

            # Set cookie and return success
            response = make_response(jsonify({'status': 'success', 'message': 'Login successful'}))
            response.set_cookie(
                'session_id', 
                session_id, 
                max_age=SESSION_EXPIRY, 
                httponly=True,
                samesite='Strict', 
                secure=request.is_secure
            )
            return response
        else:
            return jsonify({'status': 'error', 'message': 'Invalid password'}), 401

    @app.route('/logout')
    def logout():
        session_id = request.cookies.get('session_id')
        if session_id:
            # Delete session from Redis
            redis_client.delete(f"session:{session_id}")
        
        # Clear cookie
        response = make_response(jsonify({'status': 'success', 'message': 'Logged out successfully'}))
        response.delete_cookie('session_id')
        return response