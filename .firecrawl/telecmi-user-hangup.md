[Skip to main content](https://doc.telecmi.com/chub/docs/user-hangup/#__docusaurus_skipToContent_fallback)

[![TeleCMI Doc](https://doc.telecmi.com/chub/img/logo.svg)](https://doc.telecmi.com/chub/)[Home](https://doc.telecmi.com/) [Webhooks](https://doc.telecmi.com/chub/docs/webhooks-overview) [Click to call](https://doc.telecmi.com/chub/docs/click-to-call-admin) [SMS API](https://doc.telecmi.com/chub/docs/agent-send-sms)

[Examples](https://doc.telecmi.com/chub/docs/webhooks-node) [GitHub](https://github.com/telecmi/)

- [Introduction](https://doc.telecmi.com/chub/docs/user-hangup/#)

- [Webhooks](https://doc.telecmi.com/chub/docs/user-hangup/#)

- [API's](https://doc.telecmi.com/chub/docs/user-hangup/#)

- [Admin Call API](https://doc.telecmi.com/chub/docs/user-hangup/#)

- [Admin Call Flow API](https://doc.telecmi.com/chub/docs/user-hangup/#)

- [User Operations](https://doc.telecmi.com/chub/docs/user-hangup/#)

- [User API](https://doc.telecmi.com/chub/docs/user-hangup/#)

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
- [SMS API](https://doc.telecmi.com/chub/docs/user-hangup/#)

- [App Settings API](https://doc.telecmi.com/chub/docs/user-hangup/#)

- [SDK](https://doc.telecmi.com/chub/docs/user-hangup/#)

- [Examples](https://doc.telecmi.com/chub/docs/user-hangup/#)

- [Tools](https://doc.telecmi.com/chub/docs/user-hangup/#)


- User API
- User Hangup

On this page

# User Hangup

The user hangup API is used to disconnect the call, between TeleCMI softphone and phone number. After getting the [user login token](https://doc.telecmi.com/chub/docs/login-token) and ongoing call cmiuuid, make a **POST** request to the below base URL to hangup the user ongoing call.

## Base URL [​](https://doc.telecmi.com/chub/docs/user-hangup/\#base-url "Direct link to Base URL")

Send your **POST** method request with valid parameters, to the following base URL.

```text
https://rest.telecmi.com/v2/c2c/hangup
```

## Required Parameters [​](https://doc.telecmi.com/chub/docs/user-hangup/\#required-parameters "Direct link to Required Parameters")

These are the required **POST** method parameters with description

| Parameter Name | Type | Description |
| --- | --- | --- |
| token | string | Your [user token](https://doc.telecmi.com/chub/docs/login-token) |
| cmiuuid | string | [A unique identifier of Leg B call](https://doc.telecmi.com/chub/docs/live-outgoing-started) |

##### Note

All the above parameters ares mandatory.

## Sample JSON Request [​](https://doc.telecmi.com/chub/docs/user-hangup/\#sample-json-request "Direct link to Sample JSON Request")

Below is the following sample JSON **POST** method request

```javascript
{
  "token": "xxxx-xxxx-xxxx-xxxx",
  "cmiuuid": "ccd826f9-67a4-4467-acfb-a53093ff5c9d"
}
```

## Sample Response [​](https://doc.telecmi.com/chub/docs/user-hangup/\#sample-response "Direct link to Sample Response")

If the provided information is valid, your web server will get a sample response from TeleCMI Platform as given below

```javascript
{
    "code": 200,
    "msg": "call ended"
}
```

## Properties [​](https://doc.telecmi.com/chub/docs/user-hangup/\#properties "Direct link to Properties")

These are the list of properties and its description

| Property | Type | Description |
| --- | --- | --- |
| msg | string | The call ended |

## HTTP status codes [​](https://doc.telecmi.com/chub/docs/user-hangup/\#http-status-codes "Direct link to HTTP status codes")

TeleCMI API platform represents the following status code to identity the errors.

| Status code | Status Type | Description |
| --- | --- | --- |
| 200 | OK | We received the request |
| 404 | Error | Failed to authenticate token, cmiuuid not found |

[Previous\\
\\
Click-To-Call Hangup](https://doc.telecmi.com/chub/docs/click-to-call-hangup) [Next\\
\\
User Logout](https://doc.telecmi.com/chub/docs/user-logout)

- [Base URL](https://doc.telecmi.com/chub/docs/user-hangup/#base-url)
- [Required Parameters](https://doc.telecmi.com/chub/docs/user-hangup/#required-parameters)
- [Sample JSON Request](https://doc.telecmi.com/chub/docs/user-hangup/#sample-json-request)
- [Sample Response](https://doc.telecmi.com/chub/docs/user-hangup/#sample-response)
- [Properties](https://doc.telecmi.com/chub/docs/user-hangup/#properties)
- [HTTP status codes](https://doc.telecmi.com/chub/docs/user-hangup/#http-status-codes)