[Skip to main content](https://doc.telecmi.com/chub/docs/agent-get-break/#__docusaurus_skipToContent_fallback)

[![TeleCMI Doc](https://doc.telecmi.com/chub/img/logo.svg)](https://doc.telecmi.com/chub/)[Home](https://doc.telecmi.com/) [Webhooks](https://doc.telecmi.com/chub/docs/webhooks-overview) [Click to call](https://doc.telecmi.com/chub/docs/click-to-call-admin) [SMS API](https://doc.telecmi.com/chub/docs/agent-send-sms)

[Examples](https://doc.telecmi.com/chub/docs/webhooks-node) [GitHub](https://github.com/telecmi/)

- [Introduction](https://doc.telecmi.com/chub/docs/agent-get-break/#)

- [Webhooks](https://doc.telecmi.com/chub/docs/agent-get-break/#)

- [API's](https://doc.telecmi.com/chub/docs/agent-get-break/#)

- [Admin Call API](https://doc.telecmi.com/chub/docs/agent-get-break/#)

- [Admin Call Flow API](https://doc.telecmi.com/chub/docs/agent-get-break/#)

- [User Operations](https://doc.telecmi.com/chub/docs/agent-get-break/#)

- [User API](https://doc.telecmi.com/chub/docs/agent-get-break/#)

  - [Login Token](https://doc.telecmi.com/chub/docs/login-token)
  - [User Access API](https://doc.telecmi.com/chub/docs/user-access)
  - [Click-To-Call](https://doc.telecmi.com/chub/docs/click-to-call)
  - [Click-To-Call India](https://doc.telecmi.com/chub/docs/click-to-call-ind)
  - [Click-To-Call Hangup](https://doc.telecmi.com/chub/docs/click-to-call-hangup)
  - [User Hangup](https://doc.telecmi.com/chub/docs/user-hangup)
  - [User Logout](https://doc.telecmi.com/chub/docs/user-logout)
  - [User Incoming Calls](https://doc.telecmi.com/chub/docs/agent-incoming)
  - [User Outgoing Calls](https://doc.telecmi.com/chub/docs/agent-outgoing)
  - [Incoming Missed](https://doc.telecmi.com/chub/docs/agent-incoming-missed)
  - [Incoming Answered](https://doc.telecmi.com/chub/docs/agent-incoming-answered)
  - [Outgoing Missed](https://doc.telecmi.com/chub/docs/agent-outgoing-missed)
  - [Outgoing Answered](https://doc.telecmi.com/chub/docs/agent-outgoing-answered)
  - [Callback](https://doc.telecmi.com/chub/docs/agent-callback)
  - [Callback Action](https://doc.telecmi.com/chub/docs/agent-callback-action)
  - [Get Contact](https://doc.telecmi.com/chub/docs/agent-get-contact)
  - [Update Contact](https://doc.telecmi.com/chub/docs/agent-update-contact)
  - [Delete Contact](https://doc.telecmi.com/chub/docs/agent-delete-contact)
  - [Add Notes](https://doc.telecmi.com/chub/docs/agent-add-notes)
  - [Get Notes](https://doc.telecmi.com/chub/docs/agent-get-notes)
  - [Get Tags](https://doc.telecmi.com/chub/docs/agent-get-tags)
  - [Get Break](https://doc.telecmi.com/chub/docs/agent-get-break)
  - [User CallerID List](https://doc.telecmi.com/chub/docs/agent-callerid-list)
  - [User CallerID Update](https://doc.telecmi.com/chub/docs/agent-callerid-update)
  - [Add Supervisor](https://doc.telecmi.com/chub/docs/add-supervisor)
- [SMS API](https://doc.telecmi.com/chub/docs/agent-get-break/#)

- [App Settings API](https://doc.telecmi.com/chub/docs/agent-get-break/#)

- [SDK](https://doc.telecmi.com/chub/docs/agent-get-break/#)

- [Examples](https://doc.telecmi.com/chub/docs/agent-get-break/#)

- [Tools](https://doc.telecmi.com/chub/docs/agent-get-break/#)


- User API
- Get Break

On this page

# Get Break

Each user API request in TeleCMI platform includes [user login token](https://doc.telecmi.com/chub/docs/login-token). After getting the [user login token](https://doc.telecmi.com/chub/docs/login-token), make a **POST** request to the below base URL to get the remaining break hours for the user.

## Base URL [​](https://doc.telecmi.com/chub/docs/agent-get-break/\#base-url "Direct link to Base URL")

Send your **POST** method request with valid parameters, to the following base URL.

```text
https://rest.telecmi.com/v2/user_get_break
```

## Required Parameters [​](https://doc.telecmi.com/chub/docs/agent-get-break/\#required-parameters "Direct link to Required Parameters")

These are the required **POST** method parameters with description

| Parameter Name | Type | Description |
| --- | --- | --- |
| \*token | string | Your [user token](https://doc.telecmi.com/chub/docs/login-token) |
| from\_date | number | The timestamp of start date and time in UTC timezone. By default the timestamp will be last 24 hours from current time. |
| end\_date | number | The timestamp of end date and time in UTC timezone. By default the timestamp will be current time. |

##### Note

The \* marked parameter is mandatory.

## Sample JSON Request [​](https://doc.telecmi.com/chub/docs/agent-get-break/\#sample-json-request "Direct link to Sample JSON Request")

Below is the following sample JSON **POST** method request

```javascript
{
    "token": "xxxx-xxxx-xxxx-xxxx",
    "from_date" : 1654079796000,
    "end_date" : 1655980596000
}
```

## Sample Response [​](https://doc.telecmi.com/chub/docs/agent-get-break/\#sample-response "Direct link to Sample Response")

If the provided information is valid, your web server will get a sample response from TeleCMI Platform as given below

```javascript
{
    "code": 200,
    "break_sec": "00:35:00"
}
```

## Properties [​](https://doc.telecmi.com/chub/docs/agent-get-break/\#properties "Direct link to Properties")

These are the list of properties and its description

| Property | Type | Description |
| --- | --- | --- |
| break\_sec | string | The available break time in HH:MM:SS |

## HTTP status codes [​](https://doc.telecmi.com/chub/docs/agent-get-break/\#http-status-codes "Direct link to HTTP status codes")

TeleCMI API platform represents the following status code to identity the errors.

| Status code | Status Type | Description |
| --- | --- | --- |
| 200 | Success | We received the request |
| 404 | Error | Invalid user token, Failed to authenticate token |
| 400 | Error | Parameter missing |

[Previous\\
\\
Get Tags](https://doc.telecmi.com/chub/docs/agent-get-tags) [Next\\
\\
User CallerID List](https://doc.telecmi.com/chub/docs/agent-callerid-list)

- [Base URL](https://doc.telecmi.com/chub/docs/agent-get-break/#base-url)
- [Required Parameters](https://doc.telecmi.com/chub/docs/agent-get-break/#required-parameters)
- [Sample JSON Request](https://doc.telecmi.com/chub/docs/agent-get-break/#sample-json-request)
- [Sample Response](https://doc.telecmi.com/chub/docs/agent-get-break/#sample-response)
- [Properties](https://doc.telecmi.com/chub/docs/agent-get-break/#properties)
- [HTTP status codes](https://doc.telecmi.com/chub/docs/agent-get-break/#http-status-codes)