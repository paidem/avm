import json
import os
from flask import request

def check_header_auth():
    ###Check if current user from header is authenticated"""
    forwarded_user_header_name = os.environ.get('USERNAME_HEADER',None)
    admin_users_str = os.environ.get('ADMIN_USERS',None)
    if forwarded_user_header_name and admin_users_str:
        header_user = request.headers.get('X-Forwarded-Preferred-Username', None)
        admin_users = [user.strip() for user in admin_users_str.split(',') if user.strip()]
        if (header_user in admin_users):
           return True
    return False