# users/backends.py
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q

User = get_user_model()


class EmailOrUsernameModelBackend(ModelBackend):
    """
    Custom authentication backend that allows users to login with either
    their username or email address.
    """
    
    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(User.USERNAME_FIELD)
        
        if username is None or password is None:
            return None
        
        # Try to find user by username or email
        try:
            # Look for user with matching username or email
            user = User.objects.get(
                Q(username__iexact=username) | Q(email__iexact=username)
            )
        except User.DoesNotExist:
            # User doesn't exist
            return None
        except User.MultipleObjectsReturned:
            # Multiple users found (shouldn't happen with proper constraints)
            # Try username first, then email
            try:
                user = User.objects.get(username__iexact=username)
            except User.DoesNotExist:
                try:
                    user = User.objects.get(email__iexact=username)
                except User.DoesNotExist:
                    return None
        
        # Check password and user permissions
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        
        return None


# If using the custom backend, add to settings.py:
"""
AUTHENTICATION_BACKENDS = [
    'users.backends.EmailOrUsernameModelBackend',  # Custom backend first
    'django.contrib.auth.backends.ModelBackend',   # Default backend as fallback
]
"""