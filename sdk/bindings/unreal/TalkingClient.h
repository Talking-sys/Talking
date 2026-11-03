// Talking game-embed SDK — Unreal / C++ interface sketch (not a shippable plugin).
// Planned: HTTP via IHttpModule / FHttpModule against /api/sdk/v1.
// MIT (sdk/LICENSE). Never bake the API key into a pak; inject at runtime.
// This header is documentation. It is not part of the Unreal build.

#pragma once

#include "CoreMinimal.h"
#include "HttpModule.h"

struct FTalkingConfig
{
	FString ServerUrl;
	FString ApiKey;       // do not log
	FString AppId;
	float TimeoutSeconds = 15.f;
	int32 MaxRetries = 3;
	bool bOffline = false;
	FString TargetLanguage;
};

struct FTalkingLanguage
{
	FString Code;
	FString EnglishName;
	FString RussianName;
};

struct FTalkingTranslation
{
	FString RequestId;
	FString Source;
	FString Target;
	FString Text;
	FString Translation;
	FString Provider;
	int32 ElapsedMs = 0;
	TArray<uint8> Audio;  // empty unless ReturnAudio
	FString AudioMime;
};

/**
 * Async-friendly Unreal client sketch. Real implementation should use
 * FHttpRequestPtr, retry on 429/503, and never print Authorization headers.
 * Blueprint wrappers (UTalkingClient) are a later milestone — see docs/sdk/PLAN.md.
 */
class TALKINGSDK_API FTalkingClient
{
public:
	explicit FTalkingClient(const FTalkingConfig& Config);

	void SetTargetLanguage(const FString& Code);
	FString GetTargetLanguage() const;

	void ListLanguages(TFunction<void(const TArray<FTalkingLanguage>&, const FString& Error)> OnDone);
	void TranslateText(const FString& Text, const FString& Source, const FString& Target,
		bool bReturnAudio, TFunction<void(const FTalkingTranslation&, const FString& Error)> OnDone);
	void TranslateAudio(const TArray<uint8>& Audio, const FString& Format, const FString& Target,
		bool bReturnAudio, TFunction<void(const FTalkingTranslation&, const FString& Error)> OnDone);
};
