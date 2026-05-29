[Skip to main content](https://doc.telecmi.com/chub/docs/agent-outgoing/#__docusaurus_skipToContent_fallback)

[![TeleCMI Doc](https://doc.telecmi.com/chub/img/logo.svg)](https://doc.telecmi.com/chub/)[Home](https://doc.telecmi.com/) [Webhooks](https://doc.telecmi.com/chub/docs/webhooks-overview) [Click to call](https://doc.telecmi.com/chub/docs/click-to-call-admin) [SMS API](https://doc.telecmi.com/chub/docs/agent-send-sms)

[Examples](https://doc.telecmi.com/chub/docs/webhooks-node) [GitHub](https://github.com/telecmi/)

- [Introduction](https://doc.telecmi.com/chub/docs/agent-outgoing/#)

- [Webhooks](https://doc.telecmi.com/chub/docs/agent-outgoing/#)

- [API's](https://doc.telecmi.com/chub/docs/agent-outgoing/#)

- [Admin Call API](https://doc.telecmi.com/chub/docs/agent-outgoing/#)

- [Admin Call Flow API](https://doc.telecmi.com/chub/docs/agent-outgoing/#)

- [User Operations](https://doc.telecmi.com/chub/docs/agent-outgoing/#)

- [User API](https://doc.telecmi.com/chub/docs/agent-outgoing/#)

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
- [SMS API](https://doc.telecmi.com/chub/docs/agent-outgoing/#)

- [App Settings API](https://doc.telecmi.com/chub/docs/agent-outgoing/#)

- [SDK](https://doc.telecmi.com/chub/docs/agent-outgoing/#)

- [Examples](https://doc.telecmi.com/chub/docs/agent-outgoing/#)

- [Tools](https://doc.telecmi.com/chub/docs/agent-outgoing/#)


- User API
- User Outgoing Calls

On this page

# User Outgoing Calls API

Each user API request in TeleCMI platform includes [user login token](https://doc.telecmi.com/chub/docs/login-token). After getting the [user login token](https://doc.telecmi.com/chub/docs/login-token), make a **POST** request to the below base URL to retrieve the user outgoing missed and answered call details.

## Base URL [​](https://doc.telecmi.com/chub/docs/agent-outgoing/\#base-url "Direct link to Base URL")

Send your **POST** method request with valid parameters, to the following base URL.

```text
https://rest.telecmi.com/v2/user/out_cdr
```

## Required Parameters [​](https://doc.telecmi.com/chub/docs/agent-outgoing/\#required-parameters "Direct link to Required Parameters")

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

## Sample JSON Request [​](https://doc.telecmi.com/chub/docs/agent-outgoing/\#sample-json-request "Direct link to Sample JSON Request")

Below is the following sample JSON **POST** method request

```javascript
{
  "type": 1,
  "token": "xxxx-xxxx-xxxx-xxxx",
  "from": 1569911400000,
  "to": 1570167249853,
  "page": 1,
  "limit": 10
}
```

## Sample Response [​](https://doc.telecmi.com/chub/docs/agent-outgoing/\#sample-response "Direct link to Sample Response")

If the provided information is valid, your web server will get a sample response from TeleCMI Platform as given below

```javascript
{
    "count": 249,
    "cdr": [\
        {\
            "cmiuid": "1586633b-a7f9-48d4-b934-4f9befd32a0e",\
            "duration": 15,\
            "billedsec": 1,\
            "filename": "1641771_1000026329497_101_2222223.wav",\
            "rate": 0.1001,\
            "record": "true",\
            "name": "unknown",\
            "from": "442000000000",\
            "to": 37100000000,\
            "time": 1641787135771,\
            "region": "LV"\
        },\
        {\
            "cmiuid": "9ab1d5a9-16da-40c4-9006-0f177732fe23",\
            "duration": 5,\
            "billedsec": 3,\
            "filename": "16454_1000014001664_101_2222223.wav",\
            "rate": 0.0011,\
            "record": "true",\
            "name": "unknown",\
            "from": "442000000000",\
            "to": 442000000002,\
            "time": 1641552750154,\
            "region": "GB"\
        },\
        {\
            "cmiuid": "5c5dd828-b867-485a-af4d-27aa19ffe64b",\
            "duration": 4,\
            "billedsec": 3,\
            "filename": "164121845_1000031521545_101_2222223.wav",\
            "rate": 0.0011,\
            "record": "true",\
            "name": "unknown",\
            "from": "442000000000",\
            "to": 442000000004,\
            "time": 1641552721845,\
            "region": "GB"\
        },\
        {\
            "cmiuid": "a870a2e8-359b-4d69-938c-7aee7bbbd718",\
            "duration": 26,\
            "billedsec": 25,\
            "filename": "1641473811_1000010226748_101_2222223.wav",\
            "rate": 0.0096,\
            "record": "true",\
            "name": "unknown",\
            "from": "442000000000",\
            "to": 442000000001,\
            "time": 1641552473811,\
            "region": "GB"\
        },\
        {\
            "cmiuid": "1e042fc3-0100-4a78-ae5c-dae7667290a1",\
            "duration": 8,\
            "billedsec": 6,\
            "filename": "1641518_1000053394104_101_2222223.wav",\
            "rate": 0.0023,\
            "record": "true",\
            "name": "unknown",\
            "from": "442000000000",\
            "to": 442000000003,\
            "time": 1641551534318,\
            "region": "GB"\
        },\
        {\
            "cmiuid": "623a61e8-f05b-4d3b-8c6b-95a4278b9893",\
            "duration": 7,\
            "billedsec": 3,\
            "filename": "16423252_1000094279153_101_2222223.wav",\
            "rate": 0.0011,\
            "record": "true",\
            "name": "unknown",\
            "from": "442000000000",\
            "to": 442000000005,\
            "time": 1641551423252,\
            "region": "GB"\
        },\
        {\
            "cmiuid": "8162215d-c2a9-487c-8ffc-773f613939d1",\
            "duration": 7,\
            "notes": [\
                {\
                    "msg": "Sales Lead",\
                    "date": 1655389002028,\
                    "agent": "101_2222223"\
                },\
                {\
                    "msg": "Need discount on service",\
                    "date": 1641549977494,\
                    "agent": "101_2222223"\
                }\
            ],\
            "billedsec": 6,\
            "filename": "16415494_1000060339754_101_2222223.wav",\
            "rate": 0.0023,\
            "record": "true",\
            "name": "unknown",\
            "from": "442000000000",\
            "to": 442000000008,\
            "time": 1641549977494,\
            "region": "GB"\
        },\
        {\
            "cmiuid": "d12ba2e3-3501-455a-afd3-79d5b50cd4f7",\
            "duration": 17,\
            "billedsec": 11,\
            "filename": "16863420_1000010627570_101_2222223.wav",\
            "rate": 0.0046,\
            "record": "true",\
            "name": "unknown",\
            "from": "442000000000",\
            "to": 442000000002,\
            "time": 1641549863420,\
            "region": "GB"\
        },\
        {\
            "cmiuid": "bc29f22c-e9a4-45e4-bb71-268b4362861b",\
            "duration": 6,\
            "billedsec": 3,\
            "filename": "1644876_1000035097858_101_2222223.wav",\
            "rate": 0.0011,\
            "record": "true",\
            "name": "unknown",\
            "from": "442000000000",\
            "to": 442000000001,\
            "time": 1641549283996,\
            "region": "GB"\
        },\
        {\
            "cmiuid": "d935fa11-ba0f-420f-830f-6eace4a189a5",\
            "duration": 18,\
            "billedsec": 6,\
            "filename": "17924357_1000043750482_101_2222223.wav",\
            "rate": 0.002,\
            "record": "true",\
            "name": "unknown",\
            "from": "442000000000",\
            "to": 919000000000,\
            "time": 1641547924357,\
            "region": "IN"\
        }\
    ],
    "code": 200
}
```

## Properties [​](https://doc.telecmi.com/chub/docs/agent-outgoing/\#properties "Direct link to Properties")

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
| filename | string | The file name of the recorded conversation |
| rate | number | The call cost of the call |
| record | string | Call recording is enabled |
| name | string | The name of the caller |
| from | number | The CallerID for this call |
| to | number | The number the call was made to |
| time | number | Timestamp of the call in UTC timezone |
| region | string | The destination country ISO code |

## HTTP status codes [​](https://doc.telecmi.com/chub/docs/agent-outgoing/\#http-status-codes "Direct link to HTTP status codes")

TeleCMI API platform represents the following status code to identity the errors.

| Status code | Status type | Description |
| --- | --- | --- |
| 200 | OK | We received the request |
| 404 | Error | Invalid user token, Failed to authenticate token |
| 400 | Error | Parameter missing, Parameter limit lesser then equals 10 |

[Previous\\
\\
User Incoming Calls](https://doc.telecmi.com/chub/docs/agent-incoming) [Next\\
\\
Incoming Missed](https://doc.telecmi.com/chub/docs/agent-incoming-missed)

- [Base URL](https://doc.telecmi.com/chub/docs/agent-outgoing/#base-url)
- [Required Parameters](https://doc.telecmi.com/chub/docs/agent-outgoing/#required-parameters)
- [Sample JSON Request](https://doc.telecmi.com/chub/docs/agent-outgoing/#sample-json-request)
- [Sample Response](https://doc.telecmi.com/chub/docs/agent-outgoing/#sample-response)
- [Properties](https://doc.telecmi.com/chub/docs/agent-outgoing/#properties)
- [HTTP status codes](https://doc.telecmi.com/chub/docs/agent-outgoing/#http-status-codes)