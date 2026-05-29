[Skip to main content](https://doc.telecmi.com/chub/docs/agent-callerid-update/#__docusaurus_skipToContent_fallback)

[![TeleCMI Doc](https://doc.telecmi.com/chub/img/logo.svg)](https://doc.telecmi.com/chub/)[Home](https://doc.telecmi.com/) [Webhooks](https://doc.telecmi.com/chub/docs/webhooks-overview) [Click to call](https://doc.telecmi.com/chub/docs/click-to-call-admin) [SMS API](https://doc.telecmi.com/chub/docs/agent-send-sms)

[Examples](https://doc.telecmi.com/chub/docs/webhooks-node) [GitHub](https://github.com/telecmi/)

- [Introduction](https://doc.telecmi.com/chub/docs/agent-callerid-update/#)

- [Webhooks](https://doc.telecmi.com/chub/docs/agent-callerid-update/#)

- [API's](https://doc.telecmi.com/chub/docs/agent-callerid-update/#)

- [Admin Call API](https://doc.telecmi.com/chub/docs/agent-callerid-update/#)

- [Admin Call Flow API](https://doc.telecmi.com/chub/docs/agent-callerid-update/#)

- [User Operations](https://doc.telecmi.com/chub/docs/agent-callerid-update/#)

- [User API](https://doc.telecmi.com/chub/docs/agent-callerid-update/#)

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
- [SMS API](https://doc.telecmi.com/chub/docs/agent-callerid-update/#)

- [App Settings API](https://doc.telecmi.com/chub/docs/agent-callerid-update/#)

- [SDK](https://doc.telecmi.com/chub/docs/agent-callerid-update/#)

- [Examples](https://doc.telecmi.com/chub/docs/agent-callerid-update/#)

- [Tools](https://doc.telecmi.com/chub/docs/agent-callerid-update/#)


- User API
- User CallerID Update

On this page

# User CallerID Update

Each API request in TeleCMI platform includes [App id](https://doc.telecmi.com/chub/docs/app-auth) and [secret](https://doc.telecmi.com/chub/docs/app-auth). Get your [App id](https://doc.telecmi.com/chub/docs/app-auth) and [secret](https://doc.telecmi.com/chub/docs/app-auth) in [TeleCMI dashboard](https://connle.telecmi.com/login). After getting the app id and secret, make a **POST** request to the below base URL to update the callerID for user.

## Base URL [​](https://doc.telecmi.com/chub/docs/agent-callerid-update/\#base-url "Direct link to Base URL")

Send your **POST** method request with valid parameters, to the following base URL.

```text
https://rest.telecmi.com/v2/set_callerid
```

## Required Parameters [​](https://doc.telecmi.com/chub/docs/agent-callerid-update/\#required-parameters "Direct link to Required Parameters")

These are the required **POST** method parameters with description

| Parameter Name | Type | Description |
| --- | --- | --- |
| token | string | Your [user token](https://doc.telecmi.com/chub/docs/login-token) |
| callerid | string | The CallerID of the user |

##### Note

All the above parameters are mandatory.

## Sample JSON Request [​](https://doc.telecmi.com/chub/docs/agent-callerid-update/\#sample-json-request "Direct link to Sample JSON Request")

Below is the following sample JSON **POST** method request

```javascript
{
  "token": "xxxx-xxxx-xxxx-xxxx",
  "callerid": 440000000000
}
```

## Sample Response [​](https://doc.telecmi.com/chub/docs/agent-callerid-update/\#sample-response "Direct link to Sample Response")

If the provided information is valid, your web server will get a sample response from TeleCMI Platform as given below

```javascript
{
    "code": 200,
    "msg": "caller ID updated successfully",
    "callerid": 440000000000
}
```

## Properties [​](https://doc.telecmi.com/chub/docs/agent-callerid-update/\#properties "Direct link to Properties")

These are the list of properties and its description

| Property | Type | Description |
| --- | --- | --- |
| msg | string | Caller ID updated to the user |
| callerid | number | The updated caller ID number |

## HTTP status codes [​](https://doc.telecmi.com/chub/docs/agent-callerid-update/\#http-status-codes "Direct link to HTTP status codes")

TeleCMI API platform represents the following status code to identity the errors.

| Status code | Status Type | Description |
| --- | --- | --- |
| 200 | Success | We received the request, caller ID updated successfully |
| 404 | Error | Invalid user token, Failed to authenticate token |
| 400 | Error | Parameter missing |
| 501 | Error | Not implemented |

[Previous\\
\\
User CallerID List](https://doc.telecmi.com/chub/docs/agent-callerid-list) [Next\\
\\
Add Supervisor](https://doc.telecmi.com/chub/docs/add-supervisor)

- [Base URL](https://doc.telecmi.com/chub/docs/agent-callerid-update/#base-url)
- [Required Parameters](https://doc.telecmi.com/chub/docs/agent-callerid-update/#required-parameters)
- [Sample JSON Request](https://doc.telecmi.com/chub/docs/agent-callerid-update/#sample-json-request)
- [Sample Response](https://doc.telecmi.com/chub/docs/agent-callerid-update/#sample-response)
- [Properties](https://doc.telecmi.com/chub/docs/agent-callerid-update/#properties)
- [HTTP status codes](https://doc.telecmi.com/chub/docs/agent-callerid-update/#http-status-codes)