# Frontend OAuth Callback Fix

Add this code to your `Integrations.tsx` component to handle OAuth callback:

```typescript
// Add this useEffect AFTER the existing useEffects
useEffect(() => {
  const handleOAuthCallback = async () => {
    const code = searchParams.get('code');
    const state = searchParams.get('state');

    // Check if we have OAuth callback params
    if (!code || !state) return;

    // Clear the params from URL immediately to prevent re-processing
    searchParams.delete('code');
    searchParams.delete('state');
    setSearchParams(searchParams, { replace: true });

    try {
      toast.loading('Completing connection...', { id: 'oauth-callback' });

      // Get Google Sheets integration ID from the integrations list
      const googleSheetsIntegration = integrationsData?.results?.find(
        (int) => int.type === 'GOOGLE_SHEETS' || int.name.toLowerCase().includes('google')
      );

      if (!googleSheetsIntegration) {
        throw new Error('Google Sheets integration not found');
      }

      // Call the backend oauth_callback endpoint
      const response = await fetch('/api/integrations/connections/oauth_callback/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${localStorage.getItem('access_token')}`, // Adjust based on your auth
        },
        body: JSON.stringify({
          code,
          state,
          integration_id: googleSheetsIntegration.id,
          connection_name: 'Google Sheets Connection',
        }),
      });

      const data = await response.json();

      toast.dismiss('oauth-callback');

      if (response.ok) {
        toast.success('Successfully connected to Google Sheets!', {
          description: 'You can now create workflows',
          duration: 5000,
        });

        // Refresh connections list
        mutateConnections();

        // Switch to connected tab
        setActiveTab('connected');
      } else {
        throw new Error(data.error || 'Connection failed');
      }
    } catch (error: any) {
      toast.dismiss('oauth-callback');
      toast.error(`Connection failed: ${error.message}`, {
        description: 'Please try again',
        duration: 7000,
      });
    }
  };

  handleOAuthCallback();
}, [searchParams, setSearchParams, mutateConnections, setActiveTab]);
```

## Alternative: Use your API hook

If you have an API hook for oauth_callback, use this instead:

```typescript
import { useCallback, useEffect } from 'react';

// Add this useEffect
useEffect(() => {
  const code = searchParams.get('code');
  const state = searchParams.get('state');

  if (code && state) {
    handleOAuthCallback(code, state);
  }
}, [searchParams]);

const handleOAuthCallback = useCallback(async (code: string, state: string) => {
  // Clear params
  searchParams.delete('code');
  searchParams.delete('state');
  setSearchParams(searchParams, { replace: true });

  try {
    toast.loading('Completing connection...', { id: 'oauth-callback' });

    // Use your API hook (adjust the import and method name)
    const result = await completeOAuthCallback({
      code,
      state,
      integration_id: 1,
      connection_name: 'Google Sheets Connection',
    });

    toast.dismiss('oauth-callback');
    toast.success('Successfully connected!');
    mutateConnections();
    setActiveTab('connected');
  } catch (error: any) {
    toast.dismiss('oauth-callback');
    toast.error(error.message || 'Connection failed');
  }
}, [searchParams, setSearchParams, mutateConnections, setActiveTab]);
```

## Important Notes:

1. **Integration ID**: Change `integration_id: 1` to the actual Google Sheets integration ID from your backend
2. **Auth Token**: Adjust `localStorage.getItem('access_token')` to match your auth implementation
3. **API Base URL**: If your API is on a different port/domain, use full URL like `http://localhost:8000/api/...`

## Test the flow:

1. Click "Connect" on Google Sheets
2. Authorize on Google
3. Google redirects to `http://localhost:3000/integrations/?code=...&state=...`
4. Frontend POSTs to backend
5. Backend saves connection
6. Frontend shows success message

Check Django logs - you should now see the POST request to oauth_callback.
