[Skip to main content](https://doc.telecmi.com/chub/docs/agent-incoming/#__docusaurus_skipToContent_fallback)

[![TeleCMI Doc](https://doc.telecmi.com/chub/img/logo.svg)](https://doc.telecmi.com/chub/)[Home](https://doc.telecmi.com/) [Webhooks](https://doc.telecmi.com/chub/docs/webhooks-overview) [Click to call](https://doc.telecmi.com/chub/docs/click-to-call-admin) [SMS API](https://doc.telecmi.com/chub/docs/agent-send-sms)

[Examples](https://doc.telecmi.com/chub/docs/webhooks-node) [GitHub](https://github.com/telecmi/)

- [Introduction](https://doc.telecmi.com/chub/docs/agent-incoming/#)

- [Webhooks](https://doc.telecmi.com/chub/docs/agent-incoming/#)

- [API's](https://doc.telecmi.com/chub/docs/agent-incoming/#)

- [Admin Call API](https://doc.telecmi.com/chub/docs/agent-incoming/#)

- [Admin Call Flow API](https://doc.telecmi.com/chub/docs/agent-incoming/#)

- [User Operations](https://doc.telecmi.com/chub/docs/agent-incoming/#)

- [User API](https://doc.telecmi.com/chub/docs/agent-incoming/#)

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
- [SMS API](https://doc.telecmi.com/chub/docs/agent-incoming/#)

- [App Settings API](https://doc.telecmi.com/chub/docs/agent-incoming/#)

- [SDK](https://doc.telecmi.com/chub/docs/agent-incoming/#)

- [Examples](https://doc.telecmi.com/chub/docs/agent-incoming/#)

- [Tools](https://doc.telecmi.com/chub/docs/agent-incoming/#)


- User API
- User Incoming Calls

On this page

# User Incoming Calls API

Each user API request in TeleCMI platform includes [user login token](https://doc.telecmi.com/chub/docs/login-token). After getting the [user login token](https://doc.telecmi.com/chub/docs/login-token), make a **POST** request to the below base URL to retrieve the user incoming missed and answered call details.

## Base URL [​](https://doc.telecmi.com/chub/docs/agent-incoming/\#base-url "Direct link to Base URL")

Send your **POST** method request with valid parameters, to the following base URL.

```text
https://rest.telecmi.com/v2/user/in_cdr
```

## Required Parameters [​](https://doc.telecmi.com/chub/docs/agent-incoming/\#required-parameters "Direct link to Required Parameters")

These are the required **POST** method parameters with description

| Parameter Name | Type | Description |
| --- | --- | --- |
| type | number | The call type, it can be missed or answered.<br>- type: 0 - missed,<br>- type: 1 - answered |
| \*token | string | Your [user token](https://doc.telecmi.com/chub/docs/login-token) |
| \*from | number | The timestamp of from date and time in UTC timezone. |
| \*to | number | The timestamp of to date and time in UTC timezone. |
| page | number | The Number of page per 10 record. By default the page is 1. |
| limit | number | The Number of record for request, By default the limit is 10. Maximum limit is 10. |

##### Note

The \* marked parameter is mandatory.

## Sample JSON Request [​](https://doc.telecmi.com/chub/docs/agent-incoming/\#sample-json-request "Direct link to Sample JSON Request")

Below is the following sample JSON **POST** method request

```javascript
{
  "type": 0,
  "token": "xxxx-xxxx-xxxx-xxxx",
  "from": 1569911400000,
  "to": 1570167249853,
  "page": 1,
  "limit": 10
}
```

## Sample Response [​](https://doc.telecmi.com/chub/docs/agent-incoming/\#sample-response "Direct link to Sample Response")

If the provided information is valid, your web server will get a sample response from TeleCMI Platform as given below

```javascript
{
    "count": 7,
    "cdr": [\
        {\
            "cmiuid": "a0b0d95b-1d58-45f4-a210-1239e29547ec",\
            "duration": 18,\
            "notes": [\
                {\
                    "msg": "Support Query",\
                    "date": 1639554230141,\
                    "agent": "101_1111112"\
                },\
                {\
                    "msg": "Regarding the order status",\
                    "date": 1639554230141,\
                    "agent": "101_1111112"\
                }\
            ],\
            "billedsec": 6,\
            "rate": 0.01,\
            "name": "unknown",\
            "from": 918000000000,\
            "time": 1639554230141\
        },\
        {\
            "cmiuid": "dc3a4178-1af4-4c60-b340-3497696b6631",\
            "duration": 21,\
            "billedsec": 15,\
            "rate": 0.01,\
            "name": "unknown",\
            "from": 919000000000,\
            "time": 1639490151341\
        },\
        {\
            "cmiuid": "eb8cd960-4015-49d0-b419-ae6955362741",\
            "duration": 32,\
            "billedsec": 29,\
            "rate": 0.01,\
            "name": "unknown",\
            "from": 16500000000,\
            "time": 1637729869399\
        },\
        {\
            "cmiuid": "88fbbebb-0e2a-43c6-bc25-651b8c3491bb",\
            "duration": 9,\
            "billedsec": 5,\
            "rate": 0.01,\
            "name": "unknown",\
            "from": 550000000000,\
            "time": 1636170495230\
        },\
        {\
            "cmiuid": "7cd0d2bf-2a50-4760-acc5-f94bc5defaa1",\
            "duration": 9,\
            "billedsec": 6,\
            "rate": 0.01,\
            "name": "unknown",\
            "from": 919000000000,\
            "time": 1635855576344\
        },\
        {\
            "cmiuid": "e1c5973b-237e-4770-813d-b6a19dde0afc",\
            "duration": 10,\
            "billedsec": 6,\
            "rate": 0.01,\
            "name": "unknown",\
            "from": 919000000000,\
            "time": 1635855539410\
        },\
        {\
            "cmiuid": "0fc6f46a-5317-48b4-b1ef-43ec7477cda3",\
            "duration": 9,\
            "billedsec": 5,\
            "rate": 0.01,\
            "name": "unknown",\
            "from": 919000000000,\
            "time": 1635855472552\
        }\
    ],
    "code": 200
}
```

## Properties [​](https://doc.telecmi.com/chub/docs/agent-incoming/\#properties "Direct link to Properties")

These are the list of properties and its description

| Property | Type | Description |
| --- | --- | --- |
| count | number | The total count of call detail record(cdr) available |
| cdr | JSON array | The list of total cdr in detail |
| cmiuid | string | A unique identifier of this call |
| duration | number | The duration of call in seconds |
| notes | JSON array | The entire notes information of the caller or calle |
| msg | string | The notes information of the caller or calle |
| date | number | Timestamp of the added notes in UTC timezone |
| agent | string | The user ID for the added note |
| billedsec | number | The billed duration of call in seconds |
| rate | number | The call cost of the call |
| name | string | The name of the caller |
| from | number | The number the call came from |
| time | number | Timestamp of the call in UTC timezone |

## HTTP status codes [​](https://doc.telecmi.com/chub/docs/agent-incoming/\#http-status-codes "Direct link to HTTP status codes")

TeleCMI API platform represents the following status code to identity the errors.

| Status code | Status Type | Description |
| --- | --- | --- |
| 200 | OK | We received the request |
| 404 | Error | Invalid user token, Failed to authenticate token |
| 400 | Error | Parameter missing, Parameter limit lesser then equals 10 |

[Previous\\
\\
User Logout](https://doc.telecmi.com/chub/docs/user-logout) [Next\\
\\
User Outgoing Calls](https://doc.telecmi.com/chub/docs/agent-outgoing)

- [Base URL](https://doc.telecmi.com/chub/docs/agent-incoming/#base-url)
- [Required Parameters](https://doc.telecmi.com/chub/docs/agent-incoming/#required-parameters)
- [Sample JSON Request](https://doc.telecmi.com/chub/docs/agent-incoming/#sample-json-request)
- [Sample Response](https://doc.telecmi.com/chub/docs/agent-incoming/#sample-response)
- [Properties](https://doc.telecmi.com/chub/docs/agent-incoming/#properties)
- [HTTP status codes](https://doc.telecmi.com/chub/docs/agent-incoming/#http-status-codes)