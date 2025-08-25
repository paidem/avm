import json
import os
import time
import uuid
from flask import request, make_response, jsonify

from modules.session.check_user_header import check_header_auth

# Session storage - simple in-memory dict
active_sessions = {}

SESSION_EXPIRY = 86400

def check_auth():
    if check_header_auth():
        return True

    """Check if the current user is authenticated"""
    session_id = request.cookies.get('session_id')
    if session_id and session_id in active_sessions:
        # Check if session is still valid
        if active_sessions[session_id]['expires'] > time.time():
            # Update expiry time on access
            active_sessions[session_id]['expires'] = time.time() + SESSION_EXPIRY
            return True
    return False

def setup_login_routes(app, ADMIN_PASSWORD):
    @app.route('/login', methods=['POST'])
    def login():
        password = request.form.get('password')
        if password == ADMIN_PASSWORD:
            # Create a new session
            session_id = str(uuid.uuid4())
            active_sessions[session_id] = {
                'created': time.time(),
                'expires': time.time() + SESSION_EXPIRY
            }

            # Set cookie and return success
            response = make_response(jsonify({'status': 'success', 'message': 'Login successful'}))
            response.set_cookie('session_id', session_id, max_age=SESSION_EXPIRY, httponly=True,
                                samesite='Strict', secure=request.is_secure)
            return response
        else:
            return jsonify({'status': 'error', 'message': 'Invalid password'}), 401

    @app.route('/logout')
    def logout():
        session_id = request.cookies.get('session_id')
        if session_id and session_id in active_sessions:
            # Remove session
            active_sessions.pop(session_id, None)

        # Clear cookie
        response = make_response(jsonify({'status': 'success', 'message': 'Logged out successfully'}))
        response.delete_cookie('session_id')
        return response

    # Clean up expired sessions periodically
    @app.before_request
    def cleanup_sessions():
        current_time = time.time()
        expired_sessions = [sid for sid, data in active_sessions.items()
                            if data['expires'] < current_time]

        for sid in expired_sessions:
            active_sessions.pop(sid, None)