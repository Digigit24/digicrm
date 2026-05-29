[Skip to main content](https://doc.telecmi.com/chub/docs/agent-callerid-list/#__docusaurus_skipToContent_fallback)

[![TeleCMI Doc](https://doc.telecmi.com/chub/img/logo.svg)](https://doc.telecmi.com/chub/)[Home](https://doc.telecmi.com/) [Webhooks](https://doc.telecmi.com/chub/docs/webhooks-overview) [Click to call](https://doc.telecmi.com/chub/docs/click-to-call-admin) [SMS API](https://doc.telecmi.com/chub/docs/agent-send-sms)

[Examples](https://doc.telecmi.com/chub/docs/webhooks-node) [GitHub](https://github.com/telecmi/)

- [Introduction](https://doc.telecmi.com/chub/docs/agent-callerid-list/#)

- [Webhooks](https://doc.telecmi.com/chub/docs/agent-callerid-list/#)

- [API's](https://doc.telecmi.com/chub/docs/agent-callerid-list/#)

- [Admin Call API](https://doc.telecmi.com/chub/docs/agent-callerid-list/#)

- [Admin Call Flow API](https://doc.telecmi.com/chub/docs/agent-callerid-list/#)

- [User Operations](https://doc.telecmi.com/chub/docs/agent-callerid-list/#)

- [User API](https://doc.telecmi.com/chub/docs/agent-callerid-list/#)

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
- [SMS API](https://doc.telecmi.com/chub/docs/agent-callerid-list/#)

- [App Settings API](https://doc.telecmi.com/chub/docs/agent-callerid-list/#)

- [SDK](https://doc.telecmi.com/chub/docs/agent-callerid-list/#)

- [Examples](https://doc.telecmi.com/chub/docs/agent-callerid-list/#)

- [Tools](https://doc.telecmi.com/chub/docs/agent-callerid-list/#)


- User API
- User CallerID List

On this page

# User CallerID List

Each user API request in TeleCMI platform includes [user login token](https://doc.telecmi.com/chub/docs/login-token). After getting the [user login token](https://doc.telecmi.com/chub/docs/login-token), make a **POST** request to the below base URL to get the list of available callerID's.

## Base URL [​](https://doc.telecmi.com/chub/docs/agent-callerid-list/\#base-url "Direct link to Base URL")

Send your **POST** method request with valid parameters, to the following base URL.

```text
https://rest.telecmi.com/v2/get_callerid
```

## Required Parameters [​](https://doc.telecmi.com/chub/docs/agent-callerid-list/\#required-parameters "Direct link to Required Parameters")

These are the required **POST** method parameters with description

| Parameter Name | Type | Description |
| --- | --- | --- |
| token | string | Your [user token](https://doc.telecmi.com/chub/docs/login-token) |

##### Note

All the above parameters are mandatory.

## Sample JSON Request [​](https://doc.telecmi.com/chub/docs/agent-callerid-list/\#sample-json-request "Direct link to Sample JSON Request")

Below is the following sample JSON **POST** method request

```javascript
{
  "token": "xxxx-xxxx-xxxx-xxxx"
}
```

## Sample Response [​](https://doc.telecmi.com/chub/docs/agent-callerid-list/\#sample-response "Direct link to Sample Response")

If the provided information is valid, your web server will get a sample response from TeleCMI Platform as given below

```javascript
{
    "code": 200,
    "callerid": [\
        {\
            "pstn": 19170000000,\
            "price": 0,\
            "capacity": 6,\
            "profile": "1003"\
        },\
        {\
            "pstn": 550000000000,\
            "price": 0.01,\
            "capacity": 6,\
            "profile": "0"\
        },\
        {\
            "pstn": 61000000000,\
            "price": 0.01,\
            "capacity": 6,\
            "profile": "0"\
        },\
        {\
            "pstn": 6500000000,\
            "price": 0.01,\
            "capacity": 6,\
            "profile": "0"\
        },\
        {\
            "pstn": 440000000000,\
            "price": 0.01,\
            "capacity": 6,\
            "profile": "0"\
        }\
    ]
}
```

## Properties [​](https://doc.telecmi.com/chub/docs/agent-callerid-list/\#properties "Direct link to Properties")

These are the list of properties and its description

| Property | Type | Description |
| --- | --- | --- |
| callerid | JSON array | The list of available CallerID |
| pstn | number | The Caller ID |
| price | number | The incoming call rate for Caller ID |
| capacity | number | The capacity for incoming and outgoing call |
| profile | string | The profile code for unlimited plan |

## HTTP status codes [​](https://doc.telecmi.com/chub/docs/agent-callerid-list/\#http-status-codes "Direct link to HTTP status codes")

TeleCMI API platform represents the following status code to identity the errors.

| Status code | Status Type | Description |
| --- | --- | --- |
| 200 | Success | We received the request |
| 404 | Error | Invalid user token, Failed to authenticate token |
| 400 | Error | Parameter missing |

[Previous\\
\\
Get Break](https://doc.telecmi.com/chub/docs/agent-get-break) [Next\\
\\
User CallerID Update](https://doc.telecmi.com/chub/docs/agent-callerid-update)

- [Base URL](https://doc.telecmi.com/chub/docs/agent-callerid-list/#base-url)
- [Required Parameters](https://doc.telecmi.com/chub/docs/agent-callerid-list/#required-parameters)
- [Sample JSON Request](https://doc.telecmi.com/chub/docs/agent-callerid-list/#sample-json-request)
- [Sample Response](https://doc.telecmi.com/chub/docs/agent-callerid-list/#sample-response)
- [Properties](https://doc.telecmi.com/chub/docs/agent-callerid-list/#properties)
- [HTTP status codes](https://doc.telecmi.com/chub/docs/agent-callerid-list/#http-status-codes)