import jwt
import requests
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login
from django.contrib.auth.views import LoginView
from django.contrib import messages
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.views import View
import logging

logger = logging.getLogger(__name__)


class SuperAdminLoginView(LoginView):
    """
    Custom login view that authenticates against SuperAdmin
    """
    template_name = 'admin/login.html'
    redirect_authenticated_user = True
    
    def get_success_url(self):
        """Redirect to admin after successful login"""
        return '/admin/'
    
    def form_valid(self, form):
        """Handle successful form submission"""
        username = form.cleaned_data.get('username')
        password = form.cleaned_data.get('password')
        
        # Authenticate against SuperAdmin
        user = authenticate(
            self.request,
            username=username,
            password=password
        )
        
        if user is not None:
            login(self.request, user)
            messages.success(
                self.request,
                f'Successfully logged in as {user.email} for tenant {user.tenant_slug}'
            )
            return redirect(self.get_success_url())
        else:
            messages.error(
                self.request,
                'Invalid credentials or CRM module not enabled for your account'
            )
            return self.form_invalid(form)
    
    def get_context_data(self, **kwargs):
        """Add extra context to template"""
        context = super().get_context_data(**kwargs)
        context.update({
            'title': 'DigiCRM Admin Login',
            'site_title': 'DigiCRM Administration',
            'site_header': 'DigiCRM Admin',
            'superadmin_url': getattr(settings, 'SUPERADMIN_URL', 'https://admin.celiyo.com'),
        })
        return context


@method_decorator(csrf_exempt, name='dispatch')
class TokenLoginView(View):
    """
    API endpoint for token-based login (for frontend integration)
    """
    
    def post(self, request):
        """Handle token login"""
        try:
            import json
            data = json.loads(request.body)
            access_token = data.get('access_token')
            
            if not access_token:
                return JsonResponse({
                    'error': 'Access token required'
                }, status=400)
            
            # Validate JWT token
            try:
                secret_key = getattr(settings, 'JWT_SECRET_KEY')
                algorithm = getattr(settings, 'JWT_ALGORITHM', 'HS256')
                
                payload = jwt.decode(access_token, secret_key, algorithms=[algorithm])
                
                # Check if CRM module is enabled
                enabled_modules = payload.get('enabled_modules', [])
                if 'crm' not in enabled_modules:
                    return JsonResponse({
                        'error': 'CRM module not enabled for this user'
                    }, status=403)
                
                # Create user and login
                from .auth_backends import TenantUser
                user = TenantUser(payload)
                
                # Store in session
                request.session['jwt_token'] = access_token
                request.session['tenant_id'] = payload.get('tenant_id')
                request.session['tenant_slug'] = payload.get('tenant_slug')
                request.session['user_data'] = payload
                
                # Set session as authenticated
                request.session['_auth_user_id'] = str(user.id)
                request.session['_auth_user_backend'] = 'common.auth_backends.SuperAdminAuthBackend'
                
                return JsonResponse({
                    'success': True,
                    'message': f'Successfully authenticated for tenant {payload.get("tenant_slug")}',
                    'redirect_url': '/admin/',
                    'user': {
                        'id': user.id,
                        'email': user.email,
                        'tenant_id': user.tenant_id,
                        'tenant_slug': user.tenant_slug,
                        'is_superuser': user.is_superuser
                    }
                })
                
            except jwt.InvalidTokenError as e:
                return JsonResponse({
                    'error': f'Invalid token: {str(e)}'
                }, status=401)
                
        except json.JSONDecodeError:
            return JsonResponse({
                'error': 'Invalid JSON data'
            }, status=400)
        except Exception as e:
            logger.error(f"Error in token login: {e}")
            return JsonResponse({
                'error': 'Internal server error'
            }, status=500)


@csrf_exempt
def superadmin_proxy_login_view(request):
    """
    Function-based view for SuperAdmin login proxy to avoid CORS issues
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    try:
        import json
        data = json.loads(request.body)
        email = data.get('email')
        password = data.get('password')
        
        logger.info(f"Login attempt for email: {email}")
        
        if not email or not password:
            return JsonResponse({
                'error': 'Email and password are required'
            }, status=400)
        
        # Call SuperAdmin login API
        superadmin_url = getattr(settings, 'SUPERADMIN_URL', 'https://admin.celiyo.com')
        login_url = f"{superadmin_url}/api/auth/login/"
        
        logger.info(f"Calling SuperAdmin API: {login_url}")
        
        response = requests.post(login_url, json={
            'email': email,
            'password': password
        }, timeout=10)
        
        logger.info(f"SuperAdmin API response status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            logger.info(f"SuperAdmin API response data: {data}")
            
            tokens = data.get('tokens', {})
            access_token = tokens.get('access')
            user_data = data.get('user', {})
            
            logger.info(f"Access token present: {bool(access_token)}")
            logger.info(f"User data: {user_data}")
            
            if access_token:
                # Validate JWT token
                try:
                    secret_key = getattr(settings, 'JWT_SECRET_KEY')
                    algorithm = getattr(settings, 'JWT_ALGORITHM', 'HS256')
                    
                    logger.info(f"Decoding JWT with secret key: {secret_key[:10]}...")
                    
                    payload = jwt.decode(access_token, secret_key, algorithms=[algorithm])
                    
                    logger.info(f"JWT payload: {payload}")
                    
                    # Check if CRM module is enabled
                    enabled_modules = payload.get('enabled_modules', [])
                    logger.info(f"Enabled modules: {enabled_modules}")
                    
                    if 'crm' not in enabled_modules:
                        logger.warning("CRM module not enabled for user")
                        return JsonResponse({
                            'error': 'CRM module not enabled for this user'
                        }, status=403)
                    
                    # Create user data from external API response and JWT payload
                    user_payload = {
                        'user_id': user_data.get('id'),
                        'email': user_data.get('email'),
                        'first_name': user_data.get('first_name', ''),
                        'last_name': user_data.get('last_name', ''),
                        'tenant_id': user_data.get('tenant'),
                        'tenant_slug': user_data.get('tenant_name'),
                        'is_super_admin': user_data.get('is_super_admin', False),
                        'permissions': payload.get('permissions', {}),
                        'enabled_modules': payload.get('enabled_modules', [])
                    }
                    
                    logger.info(f"Created user payload: {user_payload}")
                    
                    # Create user and login
                    from .auth_backends import TenantUser
                    user = TenantUser(user_payload)
                    
                    # Store in session
                    request.session['jwt_token'] = access_token
                    request.session['tenant_id'] = user_data.get('tenant')
                    request.session['tenant_slug'] = user_data.get('tenant_name')
                    request.session['user_data'] = user_payload
                    
                    # Set session as authenticated
                    request.session['_auth_user_id'] = str(user.id)
                    request.session['_auth_user_backend'] = 'common.auth_backends.SuperAdminAuthBackend'
                    
                    logger.info(f"Session data set: user_id={user.id}, tenant_id={user_data.get('tenant')}")
                    
                    return JsonResponse({
                        'success': True,
                        'message': f'Successfully authenticated for tenant {user_data.get("tenant_name")}',
                        'redirect_url': '/admin/',
                        'user': {
                            'id': user.id,
                            'email': user.email,
                            'tenant_id': user.tenant_id,
                            'tenant_slug': user.tenant_slug,
                            'is_superuser': user.is_superuser
                        }
                    })
                    
                except jwt.InvalidTokenError as e:
                    logger.error(f"JWT decode error: {e}")
                    return JsonResponse({
                        'error': f'Invalid token: {str(e)}'
                    }, status=401)
            else:
                logger.error("No access token received from SuperAdmin")
                return JsonResponse({
                    'error': 'No access token received from SuperAdmin'
                }, status=401)
        
        elif response.status_code == 401:
            logger.warning(f"Invalid credentials for {email}")
            return JsonResponse({
                'error': 'Invalid credentials. Please check your email and password.'
            }, status=401)
        elif response.status_code == 403:
            logger.warning(f"CRM module not enabled for {email}")
            return JsonResponse({
                'error': 'CRM module not enabled for your account.'
            }, status=403)
        else:
            logger.error(f"SuperAdmin API error: {response.status_code} - {response.text}")
            return JsonResponse({
                'error': f'Authentication failed: {response.status_code}'
            }, status=response.status_code)
            
    except requests.RequestException as e:
        logger.error(f"Error connecting to SuperAdmin: {e}")
        return JsonResponse({
            'error': 'Unable to connect to SuperAdmin. Please try again later.'
        }, status=503)
    except json.JSONDecodeError:
        logger.error("Invalid JSON data in request")
        return JsonResponse({
            'error': 'Invalid JSON data'
        }, status=400)
    except Exception as e:
        logger.error(f"Error in SuperAdmin proxy login: {e}")
        return JsonResponse({
            'error': 'Internal server error'
        }, status=500)


# Keep the class-based view for backward compatibility
@method_decorator(csrf_exempt, name='dispatch')
class SuperAdminProxyLoginView(View):
    """
    Proxy endpoint for SuperAdmin login to avoid CORS issues
    """
    
    def post(self, request):
        """Handle SuperAdmin login via proxy"""
        return superadmin_proxy_login_view(request)


class AdminHealthView(View):
    """
    Health check endpoint for admin
    """
    
    def get(self, request):
        """Return health status"""
        return JsonResponse({
            'status': 'healthy',
            'service': 'DigiCRM Admin',
            'superadmin_url': getattr(settings, 'SUPERADMIN_URL', 'https://admin.celiyo.com')
        })


def admin_logout_view(request):
    """
    Custom logout view that clears session data
    """
    from django.contrib.auth import logout
    
    # Clear session data
    if hasattr(request, 'session'):
        request.session.flush()
    
    logout(request)
    messages.success(request, 'Successfully logged out')
    return redirect('/admin/login/')
