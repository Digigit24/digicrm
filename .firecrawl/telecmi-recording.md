[Skip to main content](https://doc.telecmi.com/chub/docs/play-record/#__docusaurus_skipToContent_fallback)

On this page

Each API request in TeleCMI platform includes [App id](https://doc.telecmi.com/chub/docs/app-auth) and [secret](https://doc.telecmi.com/chub/docs/app-auth). Get your [App id](https://doc.telecmi.com/chub/docs/app-auth) and [secret](https://doc.telecmi.com/chub/docs/app-auth) in [TeleCMI dashboard](https://connle.telecmi.com/login). After getting the app id and secret, make a **GET** request to the below base URL to download the voicemail or recorded call.

## Base URL [​](https://doc.telecmi.com/chub/docs/play-record/\#base-url "Direct link to Base URL")

Send your **GET** method request with valid parameters, to the following base URL.

```text
https://rest.telecmi.com/v2/play?appid=1111113&secret=xx-xx&file=demo_1111113.wav
```

## Required Parameters [​](https://doc.telecmi.com/chub/docs/play-record/\#required-parameters "Direct link to Required Parameters")

These are the required **GET** method parameters with description

| Parameter Name | Type | Description |
| --- | --- | --- |
| appid | number | Your app ID |
| secret | string | Your app secret |
| file | string | The file name of the voicemail or recorded call |

##### Note

All the above parameters are mandatory.

If the provided information is valid, your web server will get a response from TeleCMI platform and you can able to stream the audio file.

## HTTP status codes [​](https://doc.telecmi.com/chub/docs/play-record/\#http-status-codes "Direct link to HTTP status codes")

TeleCMI API platform represents the following status code to identity the errors.

| Status code | Status type | Description |
| --- | --- | --- |
| 200 | OK | We received the request |
| 407 | Error | Invalid user token, authentication failed |
| 404 | Error | Parameter missing |

- [Base URL](https://doc.telecmi.com/chub/docs/play-record/#base-url)
- [Required Parameters](https://doc.telecmi.com/chub/docs/play-record/#required-parameters)
- [HTTP status codes](https://doc.telecmi.com/chub/docs/play-record/#http-status-codes)