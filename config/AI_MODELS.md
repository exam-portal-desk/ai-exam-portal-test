# AI Configuration Guide

A step-by-step manual for the AI models used by SmartAIExam. Find what you want to do, follow the numbered steps, copy the JSON, and check the result in **Admin → AI Configuration**.

## Quick Start

Pick what you want to do. Each link opens a tutorial you can follow from Step 1.

| I want to... | Go to |
|---|---|
| Add a new model | [A. Add a new model to an existing provider](#a-add-a-new-model-to-an-existing-provider) |
| Use a model I already have through another provider | [B. Use an existing model through another provider](#b-use-an-existing-model-through-another-provider) |
| Add a new provider (a new company or service) | [C. Add a completely new provider](#c-add-a-completely-new-provider) |
| Change the model used by Assistant, Explanation or Question Generation | [D. Change which model a feature uses](#d-change-which-model-a-feature-uses) |
| Switch a feature to a different provider | [M. Switch a feature from one provider to another](#m-switch-a-feature-from-one-provider-to-another) |
| Turn a model off for now | [E. Temporarily disable a model](#e-temporarily-disable-a-model) |
| Remove a model for good | [F. Permanently remove a model](#f-permanently-remove-a-model) |
| Change a provider logo | [G. Change a provider logo](#g-change-a-provider-logo) |
| Change a model logo | [H. Change a model logo](#h-change-a-model-logo) |
| Change an API key | [I. Change an API key](#i-change-an-api-key) |
| Change rate limits | [J. Change rate limits](#j-change-rate-limits) |
| Change what a model can do (text, vision, PDF) | [K. Change what a model can do](#k-change-what-a-model-can-do) |
| Add another model to a provider that already exists | [L. Add another model to a provider that already exists](#l-add-another-model-to-a-provider-that-already-exists) |
| My new model is greyed out ("API key for this provider is not configured.") | [A. Add a new model to an existing provider](#a-add-a-new-model-to-an-existing-provider), Step 5 |
| A model is slow, times out, or its replies look like it is "thinking" out loud | [N. Troubleshooting](#n-troubleshooting) |
| Something else is not working | [N. Troubleshooting](#n-troubleshooting) |

**Where you make changes.** There are three places, and each one has one job:

- **The file** `config/ai_models.json`: which providers, models, logos and limits exist, and each feature's default model.
- **The Admin page** (AI Configuration): which model each feature uses, with **Change Model**.
- **The server's environment settings** (the `.env` file or your host's settings): API keys, and nothing else.

> **Important:** this is the one rule. A model is never chosen in the environment settings. They only hold keys. If your environment settings still contain old lines that name a model, the application ignores them. You can delete them.

## The four ideas

| Idea | Means | Example | Section of the file |
|---|---|---|---|
| **Provider** | The company or API service that actually serves the model | Groq, Google Gemini | `providers` |
| **Model** | The AI model itself | GPT-OSS 120B, Gemini 2.5 Flash | `models` |
| **Offering** | The link between a provider and a model: "this provider serves this model", with the provider's own model ID, and whether that combination is switched on | Groq serves GPT-OSS 120B | `offerings` |
| **Assignment** | Which model a feature uses | Assistant Chat Replies uses GPT-OSS 120B | The Admin page (**Change Model**). `assignments` holds the default |

A model and a provider are **not** the same thing. A model has its own name, abilities and logo. A provider has its own name, logo and API details. The link between them is the offering. In the file and on the Admin page, one offering is written `provider/model`, for example `groq/gpt-oss-120b`.

### The features

Each feature has its own assignment, so changing one never changes another.

| Feature as shown in Admin | Key in the file | Needs a model that can do |
|---|---|---|
| Assistant Chat Replies | `assistant_chat` | text |
| Chat Title Generation | `assistant_title` | text |
| Explanation (text questions) | `explanation_text` | text |
| Explanation (image questions) | `explanation_vision` | vision (images) |
| Generation from text | `question_generation_text` | text |
| Generation from scanned PDFs | `question_generation_vision` | vision and PDF |

The features are defined in the `features` section. You normally never edit it. Adding a new feature needs a developer, because the application code has to use it.

> **Note:** There is no separate "RAG" feature. Question generation from a text PDF sends the PDF's text to the model, and that is part of "Generation from text".

## Do I need a new model or a new offering?

Decide this first. It stops you creating a duplicate.

| Your situation | What it is | You add |
|---|---|---|
| A completely new AI model that is not in the file | A new model | `models` and `offerings` |
| A model that is already in the file, and another provider also serves it | The same model, a new offering | `offerings` only |
| A new model from a provider you already use | A new model | `models` and `offerings` |
| A newer version or a different size of a model you have | A new model | `models` and `offerings` |

**Case 1: a completely new AI model.** Gemini X Flash has just been released and is not in your file. Add a new model definition, then an offering. Follow tutorial A.

**Case 2: the same model, another provider.** GPT-OSS 120B is already in your file, served by Groq. ExampleCo now serves it too. Do **not** create a second "GPT-OSS 120B". Keep the one model and add one more offering that says "ExampleCo serves it". Follow tutorial B.

> **Tip:** If the name and abilities would be the same, it is the same model. A duplicate would appear in Admin as a second, different model, and you would have to keep two names and two logos in step.

## Before you edit the file

- Edit `config/ai_models.json` on the server that runs the site. If the site is deployed from a repository, make the change there and deploy it. A change to a copy on your own computer does not change the live site.
- Change one thing at a time, then click **Reload configuration** on the Admin page.
- The file is strict JSON: put a comma between items but not after the last one, use double quotes, and do not add comments. Paste the file into a JSON validator if you are unsure.
- If the file has a mistake, the site keeps using the last good configuration and the Admin page tells you what is wrong. If the server restarts while the file is broken, the AI features stop until you fix it.
- Never put an API key in the file.

**When changes take effect**

| You changed | To apply it |
|---|---|
| The file | Click **Reload configuration**. No restart. Everything is also re-read automatically after about 30 seconds |
| A choice made with **Change Model** | Nothing. It applies at once |
| An API key or other environment setting | Restart the application |

## A. Add a new model to an existing provider

**Scenario:** Suppose Google has released a new model, "Gemini X Flash", and I want to be able to use it in SmartAIExam. The provider (Google Gemini) is already in my file.

**What you will change**

- `models`: describe the new model (required)
- `offerings`: connect it to its provider (required)
- `rate_limits`: optional, only if you know the real limits
- `assignments` or **Change Model**: only if you want a feature to use it

You do **not** change `providers`, `features`, or any API key.

Have this ready from the provider's documentation: the exact model ID, what the model can do, and its context and output sizes.

### Step 1. Open the file

Open `config/ai_models.json`. You will work in two sections: `"models"` and `"offerings"`. Also find `"providers"` and note the key of your provider (here `gemini`). You will type it exactly.

### Step 2. Add the model to `models`

Copy this new entry:

```json Copy this
"gemini-x-flash": {
  "display_name": "Gemini X Flash",
  "description": "Fast multimodal Gemini model from Google.",
  "capabilities": ["text", "vision", "pdf", "structured_output"],
  "ui": { "logo": "/static/ai/models/gemini.svg" }
}
```

Paste it inside `"models": { ... }`, after the last model, with a comma between them. Before:

```json Before: models
"models": {
  "gemini-2.5-flash": {
    "display_name": "Gemini 2.5 Flash",
    "description": "Fast multimodal Gemini model from Google.",
    "capabilities": ["text", "vision", "pdf", "structured_output"],
    "ui": { "logo": "/static/ai/models/gemini.svg" }
  }
}
```

After:

```json After: models
"models": {
  "gemini-2.5-flash": {
    "display_name": "Gemini 2.5 Flash",
    "description": "Fast multimodal Gemini model from Google.",
    "capabilities": ["text", "vision", "pdf", "structured_output"],
    "ui": { "logo": "/static/ai/models/gemini.svg" }
  },
  "gemini-x-flash": {
    "display_name": "Gemini X Flash",
    "description": "Fast multimodal Gemini model from Google.",
    "capabilities": ["text", "vision", "pdf", "structured_output"],
    "ui": { "logo": "/static/ai/models/gemini.svg" }
  }
}
```

What each line means:

| Field | Put here |
|---|---|
| `"gemini-x-flash"` | A short unique key you choose: lowercase letters, digits and dashes. You will use it again in Step 3 |
| `display_name` | The name shown in Admin |
| `description` | One sentence about **this** model. Do not copy another model's description |
| `capabilities` | What the model can do, **as the provider documents it**. If the documentation does not mention an ability (for example PDF input), leave it out. See tutorial K |
| `ui.logo` | The model's own logo file. Leave `ui` out if you have none |

### Step 3. Add the offering to `offerings`

Copy this entry:

```json Copy this
"gemini-x-flash": {
  "model_id": "gemini-x-flash",
  "enabled": true,
  "limits": { "context_tokens": 1048576, "max_output_tokens": 65536 }
}
```

Paste it inside `"gemini": { ... }` under `"offerings"`. The outer key `gemini` is the provider key from Step 1, and the inner key `gemini-x-flash` must be exactly the key you used in Step 2. Before:

```json Before: offerings
"offerings": {
  "gemini": {
    "gemini-2.5-flash": {
      "model_id": "gemini-2.5-flash",
      "enabled": true,
      "limits": { "context_tokens": 1048576, "max_output_tokens": 65536 }
    }
  }
}
```

After:

```json After: offerings
"offerings": {
  "gemini": {
    "gemini-2.5-flash": {
      "model_id": "gemini-2.5-flash",
      "enabled": true,
      "limits": { "context_tokens": 1048576, "max_output_tokens": 65536 }
    },
    "gemini-x-flash": {
      "model_id": "gemini-x-flash",
      "enabled": true,
      "limits": { "context_tokens": 1048576, "max_output_tokens": 65536 }
    }
  }
}
```

| Field | Put here |
|---|---|
| `model_id` | The ID the provider uses, **letter for letter** from its documentation |
| `enabled` | `true` to use it. `false` switches it off |
| `limits` | How much the model can read and write, from the provider's documentation. Leave a number out if you do not know it |

### Step 4. Add rate limits (optional)

Only if you know the real numbers. See tutorial J for what each number means. The numbers below are examples only.

```json After: rate_limits
"rate_limits": {
  "gemini/gemini-x-flash": {
    "scope": "project",
    "source": "Provider dashboard",
    "as_of": "2026-09-20",
    "limits": { "rpm": 15, "rpd": 500, "tpm": 250000 }
  }
}
```

If the file already has a `"rate_limits"` section, paste only the inner `"gemini/gemini-x-flash": { ... }` entry into it.

### Step 5. Check the provider's API key

Usually there is nothing to do here. A model uses its **provider's API key**. The provider already works, so its key is already set. A model never needs a key of its own, and you never edit `.env` to add a model.

To be sure, open **Admin → AI Configuration**. Under **Available models**, the provider's group shows one of these:

| The group shows | Means | What to do |
|---|---|---|
| **API key configured** | The provider's key is set. Every feature can use it | Nothing |
| **Dedicated keys set** | The provider has no key of its own, only keys for some features. Only those features can use the provider | Set the provider's key (below), or use only those features |
| **API key not configured** | No key at all | Set the provider's key (below) |

Also, every feature card has its own **API key** line, so you can see the answer for the feature you care about.

**To set the provider's key:** follow [tutorial I](#i-change-an-api-key), then restart the application. That one key then works for every model of that provider, in every feature. If you skip it, the model is greyed out in **Change Model** with "API key for this provider is not configured."

> **Note:** A feature can optionally have its own key for a provider, for its own usage quota (`credential_env` under `assignments`). If that setting is set, the feature uses it. If it is not set, the feature uses the provider's normal key. You never have to edit `credential_env` to add a model.

### Step 6. Save and reload

Save the file. In Admin, open **AI Configuration** and click **Reload configuration**. You do not need to restart anything.

### Step 7. Check the model appears

- Under **Available models**, in the Google Gemini group, there is a card called **Gemini X Flash**. It shows its abilities and **Not in use**.
- There are no messages at the top of the page, and **Needs Attention** is 0.
- Click the card. **Model details** shows the Model ID you typed.

### Step 8. Make a feature use it

There is no list of models per feature. A model is offered for every feature whose needs it meets. Gemini X Flash can do text, vision and PDF, so it is offered for all six.

1. Under **Where AI is used**, find the feature card, for example **Generation from text**.
2. Click **Change Model**, choose **Gemini X Flash**, and click **Save Configuration**.

Only do this for features you want to move. Adding a model never changes what any feature uses.

If the model is **greyed out** with "API key for this provider is not configured.", the provider's key is not set on the server yet. Go back to Step 5.

To make it the standard model for a feature even after an admin clicks **Reset to default**, change that feature's default in `assignments`. Only the `model` line changes:

```json After: assignments
"assignments": {
  "question_generation_text": { "model": "gemini/gemini-x-flash" }
}
```

The other lines in that feature's entry stay as they are. If the file already has an `"assignments"` section, change only that one line inside it.

### Step 9. Test the feature

Use the feature once for real, for example send a message to the AI Assistant, ask for an explanation of a wrong answer, or generate a few questions in the AI Command Centre.

### Expected result

Gemini X Flash is listed under Google Gemini, has the right logo and abilities, appears in **Change Model** for the features it can serve, and the feature you moved shows **Gemini X Flash** with **Source: Admin selection**. No other feature changed.

### Checklist

**Check before saving**

- [ ] The JSON is valid (commas between entries, none after the last)
- [ ] The provider key under `offerings` is the same as under `providers`
- [ ] The model key is the same in `models` and `offerings`
- [ ] `model_id` matches the provider's documentation exactly
- [ ] `capabilities` match what the model really supports
- [ ] `enabled` is `true`
- [ ] The provider's API key is set (Step 5)
- [ ] No API key was added to the file

**After saving**

- [ ] Clicked **Reload configuration**
- [ ] The model appears under the right provider
- [ ] There are no warning messages at the top
- [ ] The feature you changed shows the new model
- [ ] A real test of that feature works

## B. Use an existing model through another provider

**Scenario:** Suppose GPT-OSS 120B is already in my file, served by Groq. A provider called ExampleCo (added in tutorial C) also serves it, and I want to be able to use it through ExampleCo.

**What you will change**

- `offerings`: add one new entry (required)
- `rate_limits`: optional, for the new offering

You do **not** change `models`. The model already exists. Do not create it again.

### Step 1. Check the two things you need

- In `models`, the model exists (here the key `gpt-oss-120b`).
- In `providers`, the new provider exists (here `exampleco`). If it does not, do tutorial C first.

### Step 2. Add the offering

Copy this entry. The `model_id` is whatever **ExampleCo** calls the model. It may differ from Groq's ID.

```json Copy this
"gpt-oss-120b": {
  "model_id": "exampleco/gpt-oss-120b-instruct",
  "enabled": true,
  "limits": { "context_tokens": 32768 }
}
```

Paste it inside `"exampleco": { ... }` under `"offerings"`. Before:

```json Before: offerings
"offerings": {
  "groq": {
    "gpt-oss-120b": {
      "model_id": "openai/gpt-oss-120b",
      "enabled": true,
      "limits": { "context_tokens": 131072, "max_output_tokens": 65536 }
    }
  },
  "exampleco": {
    "exampleco-small": {
      "model_id": "exampleco-small-latest",
      "limits": { "context_tokens": 32768, "max_output_tokens": 4096 }
    }
  }
}
```

After:

```json After: offerings
"offerings": {
  "groq": {
    "gpt-oss-120b": {
      "model_id": "openai/gpt-oss-120b",
      "enabled": true,
      "limits": { "context_tokens": 131072, "max_output_tokens": 65536 }
    }
  },
  "exampleco": {
    "exampleco-small": {
      "model_id": "exampleco-small-latest",
      "limits": { "context_tokens": 32768, "max_output_tokens": 4096 }
    },
    "gpt-oss-120b": {
      "model_id": "exampleco/gpt-oss-120b-instruct",
      "enabled": true,
      "limits": { "context_tokens": 32768 }
    }
  }
}
```

The model key `gpt-oss-120b` is the same in both places. That is how the two offerings share one model.

### Step 3. Add rate limits (optional)

Rate limits belong to each offering, because they depend on the provider. Use the entry name `exampleco/gpt-oss-120b`. See tutorial J.

### Step 4. Save, reload and check

Click **Reload configuration**. Under **Available models**, GPT-OSS 120B now appears **twice**: once under Groq and once under ExampleCo. Both show the same name and the same model logo, but each group shows its own provider logo.

### Step 5. Use it

Click **Change Model** on a feature and pick the **ExampleCo** entry. If it is greyed out with "API key for this provider is not configured.", ExampleCo's key is not set yet. Follow Step 5 of [tutorial A](#a-add-a-new-model-to-an-existing-provider): it applies to any provider.

### Expected result

One model, two offerings. The Groq offering is unchanged and keeps working. The two offerings have separate on/off switches, limits and rate limits.

> **Careful:** the provider logo and the model logo are separate settings. Do not copy one into the other, and do not create a second model just because the provider changed.

If ExampleCo offers fewer abilities than the model normally has, add a `"capabilities"` list to **that offering**. It replaces the model's list for that provider only.

### Checklist

**Check before saving**

- [ ] JSON is valid
- [ ] The model key in the new offering is exactly the key in `models`
- [ ] The provider key exists under `providers`
- [ ] `model_id` is the new provider's own ID for the model
- [ ] `enabled` is `true`
- [ ] The provider's API key is set

**After saving**

- [ ] Clicked **Reload configuration**
- [ ] The model appears under both providers
- [ ] The new entry can be chosen with **Change Model**
- [ ] A real test of the feature works

## C. Add a completely new provider

**Scenario:** Suppose I want to use a new company or service, "ExampleCo", that SmartAIExam does not use yet.

**What you will change**

- `providers`: add the new provider (required)
- `models`: add each model it serves, if the model is new
- `offerings`: one entry per model it serves (required)
- `rate_limits`: optional, only if you know the real limits
- The server's environment settings: the provider's API key (required, needs a restart)
- The provider's logo file in `static/ai/providers/` (optional)
- `assignments` or **Change Model**: only if you want a feature to use it

### Step 1. Check how ExampleCo's API works

The application understands three API styles, called protocols:

| Protocol | Use it when the provider says it is... |
|---|---|
| `openai_compatible` | "OpenAI-compatible" (the common chat-completions style; many providers use it) |
| `gemini` | Google's Gemini API |
| `anthropic` | Anthropic's Messages API |

- If ExampleCo matches one of these, **you can do everything in the JSON file** (this tutorial).
- If it is none of these, **THIS REQUIRES CODE CHANGES**. A developer must add a small adapter first (see "For developers"). Once it exists, adding more models from that provider is file-only again.

### Step 2. Add the provider

Copy this entry:

```json Copy this
"exampleco": {
  "display_name": "ExampleCo",
  "protocol": "openai_compatible",
  "enabled": true,
  "api": {
    "base_url": "https://api.exampleco.example/v1",
    "key_env": "EXAMPLECO_API_KEY"
  },
  "ui": { "logo": "/static/ai/providers/exampleco.svg" }
}
```

Paste it inside `"providers": { ... }`, after the last provider, with a comma between them. Your file lists your own providers; this example shows one called OtherCo. Before:

```json Before: providers
"providers": {
  "otherco": {
    "display_name": "OtherCo",
    "protocol": "openai_compatible",
    "enabled": true,
    "api": {
      "base_url": "https://api.otherco.example/v1",
      "key_env": "OTHERCO_API_KEY"
    },
    "ui": { "logo": "/static/ai/providers/otherco.svg" }
  }
}
```

After:

```json After: providers
"providers": {
  "otherco": {
    "display_name": "OtherCo",
    "protocol": "openai_compatible",
    "enabled": true,
    "api": {
      "base_url": "https://api.otherco.example/v1",
      "key_env": "OTHERCO_API_KEY"
    },
    "ui": { "logo": "/static/ai/providers/otherco.svg" }
  },
  "exampleco": {
    "display_name": "ExampleCo",
    "protocol": "openai_compatible",
    "enabled": true,
    "api": {
      "base_url": "https://api.exampleco.example/v1",
      "key_env": "EXAMPLECO_API_KEY"
    },
    "ui": { "logo": "/static/ai/providers/exampleco.svg" }
  }
}
```

| Field | Put here |
|---|---|
| `"exampleco"` | A short unique key for the provider |
| `display_name` | The name shown in Admin |
| `protocol` | One of the three protocols from Step 1 |
| `api.base_url` | The provider's API address, from its documentation |
| `api.key_env` | The **name** of the setting that will hold the key (Step 3). Never the key itself |
| `api.endpoint` | Only if the path differs from the usual one (`/chat/completions` for `openai_compatible`) |
| `ui.logo` | The provider's own logo (Step 5) |

### Step 3. Set the API key in the environment

Choose the name you wrote in `key_env`, and set the real key under that name:

- **On your computer:** add a line to the `.env` file in the project's main folder.
- **On a hosting service:** add it in the service's Environment settings.

```env Copy this
EXAMPLECO_API_KEY=paste-the-real-key-here
```

Replace the placeholder with your real key. No quotes, no spaces. Never put the key in `ai_models.json`.

### Step 4. Add the models it serves and their offerings

For each model ExampleCo serves:

- If the model is **new**: add it under `models`, then under `offerings` (like tutorial A).
- If the model is **already in your file**: add only an offering (like tutorial B).

For a new model, `capabilities` is important: it decides which features can use the model (see tutorial K).

```json After: models
"models": {
  "exampleco-small": {
    "display_name": "ExampleCo Small",
    "description": "Small general-purpose model.",
    "capabilities": ["text"]
  }
}
```

```json After: offerings
"offerings": {
  "exampleco": {
    "exampleco-small": {
      "model_id": "exampleco-small-latest",
      "limits": { "context_tokens": 32768, "max_output_tokens": 4096 }
    }
  }
}
```

### Step 5. Add the logo and rate limits (both optional)

- Put the logo file in the project's `static/ai/providers/` folder, with the same name as in `ui.logo`.
- Add rate limits only if you know the real numbers (tutorial J).

### Step 6. Save and restart

Save the file, then **restart the application**. A restart is needed once, because a new API key was added.

### Step 7. Check in Admin

- **Providers** at the top lists **ExampleCo** with its logo.
- Under **Available models**, the **ExampleCo** group lists its model with its abilities.
- The group header says the API key is configured. If it says "not configured", the setting name or the restart is wrong (tutorial I).
- No messages at the top, and **Needs Attention** is 0.

### Step 8. Use it (only if you want to)

Click **Change Model** on a feature and choose the new model. Only models with the abilities the feature needs are listed.

### Expected result

The new provider and its models appear. No feature changed until you chose a model for it.

### Checklist

**Check before saving**

- [ ] JSON is valid
- [ ] `protocol` is one of the three supported protocols
- [ ] `base_url` matches the provider's documentation
- [ ] `key_env` is a name, not the key
- [ ] Every offering uses a provider key that exists
- [ ] Every model has the right `capabilities`
- [ ] The API key is set in the environment

**After saving**

- [ ] Restarted the application
- [ ] The provider and its logo appear
- [ ] The models appear under the provider
- [ ] The key shows as configured
- [ ] A real test of a feature using the new model works

## D. Change which model a feature uses

**Scenario:** Suppose I want **Assistant Chat Replies** to use a different model (Model X) instead of the one it uses now (Model Y).

This is the simplest change. You do **not** edit the model, the provider or the offering, and you do not create anything new.

**What you will change**

- The Admin page: **Change Model** on that one feature

### Step 1. Open the feature

Go to **Admin → AI Configuration**. Under **Where AI is used**, find the card **Assistant Chat Replies**. It shows the model it uses now.

### Step 2. Choose the new model

Click **Change Model**. A list of models opens. It contains only models that can do what this feature needs. A greyed-out model cannot be chosen yet, and the reason is shown under it.

### Step 3. Save

Select Model X and click **Save Configuration**. It applies at once and only to this feature.

### Step 4. Check

The card now shows Model X and **Source: Admin selection**. No other card changed. That includes **Chat Title Generation**, which has its own model.

### Step 5. Test

Send a message in the AI Assistant.

### Step 6. Undo it if needed

Click **Change Model**, then **Reset to default**. The feature returns to the default from the file.

### Changing the default in the file instead

The default is what the feature uses when nobody has chosen anything in Admin. Change only the `model` line of that feature in `assignments`, written as `provider/model`:

```json After: assignments
"assignments": {
  "assistant_chat": { "model": "gemini/gemini-x-flash" }
}
```

Leave the other lines of that entry alone.

> **Important:** a choice made with **Change Model** wins over the file. If you edit the file and nothing changes, click **Change Model** and then **Reset to default**. The environment settings never choose a model, so there is nothing to change there. If the new model comes from a different provider, read tutorial M.

### Expected result

Only **Assistant Chat Replies** uses the new model.

### Checklist

**Check before saving**

- [ ] The model is one of those offered in **Change Model**
- [ ] If editing the file: only the `model` line changed
- [ ] The value is written as `provider/model`

**After saving**

- [ ] The feature card shows the new model
- [ ] The other feature cards are unchanged
- [ ] A real test of the feature works

## E. Temporarily disable a model

**Scenario:** Suppose Gemini X Flash is having problems and I want to stop using it for now, without deleting it.

**What you will change**

- `offerings`: one field, `enabled`, on that model's entry

### Step 1. Find out what uses it

In **Available models**, the card says how many features use it. Click the card, and **Model details** lists them under **Used by**.

### Step 2. Move those features to another model

For each feature, use **Change Model** (tutorial D). **Do this first.** A feature still assigned to a disabled model stops working.

### Step 3. Switch it off

Change `enabled` from `true` to `false`:

```json Before: offerings
"offerings": {
  "gemini": {
    "gemini-x-flash": {
      "model_id": "gemini-x-flash",
      "enabled": true
    }
  }
}
```

```json After: offerings
"offerings": {
  "gemini": {
    "gemini-x-flash": {
      "model_id": "gemini-x-flash",
      "enabled": false
    }
  }
}
```

To switch off **every** model of a provider at once, set `"enabled": false` on the provider under `providers` instead (only that one line):

```json After: providers
"providers": {
  "gemini": { "enabled": false }
}
```

### Step 4. Save and reload

Click **Reload configuration**.

### Step 5. Check

The model card now shows **Disabled**, and the model is no longer offered in **Change Model**.

### What happens to existing assignments

- Nothing is deleted. Assignments, limits and logos stay as they are.
- A feature still assigned to it shows **Attention** with "Selected model is disabled." The system never switches to another model by itself, so that feature fails until you move it.
- Requests already being processed finish normally. New requests fail.
- Question generation checks the model before every batch, so a running job fails on its remaining batches.
- Students see a general "AI service unavailable" message. Chat titles quietly fall back to a simple automatic title.

### Turn it back on

Set `"enabled": true` (or delete the line) and click **Reload configuration**. Then use **Change Model** to put features back.

### Expected result

The model is listed as **Disabled**, and no feature uses it.

### Checklist

**Check before saving**

- [ ] No feature is still assigned to this model
- [ ] The field is `"enabled": false` on the right entry

**After saving**

- [ ] Clicked **Reload configuration**
- [ ] The card shows **Disabled**
- [ ] Every feature card is **Active**

## F. Permanently remove a model

**Scenario:** Suppose I no longer want Gemini X Flash and want it gone from the file.

**What you will change (in this order)**

- **Change Model** and `assignments`: move features away
- `offerings`: remove its entry
- `rate_limits`: remove its entry, if it has one
- `models`: remove it, only when no offering uses it
- The logo file: only if nothing else uses it

### Step 1. Check where it is used

The model card says how many features use it. **Model details → Used by** lists them.

### Step 2. Move those features

Use **Change Model** on each feature (tutorial D). If a feature has an Admin choice pointing at this model, change it or click **Reset to default**.

### Step 3. Check the defaults in the file

Search the file for the model's `provider/model` name (`gemini/gemini-x-flash`). If it appears under `assignments`, change that `model` line to another model.

### Step 4. Remove the offering

Delete the entry from `offerings`. Before:

```json Before: offerings
"offerings": {
  "gemini": {
    "gemini-2.5-flash": {
      "model_id": "gemini-2.5-flash",
      "enabled": true
    },
    "gemini-x-flash": {
      "model_id": "gemini-x-flash",
      "enabled": true
    }
  }
}
```

After (remember there is no comma after the last entry):

```json After: offerings
"offerings": {
  "gemini": {
    "gemini-2.5-flash": {
      "model_id": "gemini-2.5-flash",
      "enabled": true
    }
  }
}
```

If you only want to stop using it through one provider, remove just that provider's offering and stop here.

### Step 5. Remove its rate limits

Delete the `"gemini/gemini-x-flash": { ... }` entry from `rate_limits`, if there is one.

### Step 6. Remove the model

Delete it from `models`, but **only** when no offering under any provider still uses it.

### Step 7. Remove the logo file

Delete its file only if nothing else in the file uses that path. Search the file for the path first.

### Step 8. Save and reload

Click **Reload configuration**.

### Step 9. Check

No messages at the top, **Needs Attention** shows 0, and every feature card is **Active**.

> **Warning:** do not delete the model first. An offering that points at a missing model is an error. Features using it fail at once, and Admin choices that point at it show "no longer exists" until you fix them.

### Expected result

The model is gone from **Available models**, and nothing else changed.

### Checklist

**Check before saving**

- [ ] No feature uses the model any more
- [ ] No `assignments` line names it
- [ ] Its offerings and rate limits are removed
- [ ] The JSON is still valid (no comma after the last entry)

**After saving**

- [ ] Clicked **Reload configuration**
- [ ] No warning messages
- [ ] **Needs Attention** is 0

## G. Change a provider logo

**Scenario:** Suppose I want Google Gemini to show a different logo.

The provider logo and the model logo are **independent**. Changing one never changes the other.

**What you will change**

- The logo file in `static/ai/providers/`
- `providers` → the provider → `ui` → `logo`

### Step 1. Add the file

Put the new logo file in the project's `static/ai/providers/` folder, for example `gemini-new.svg`. Use SVG or PNG, and keep the file small.

### Step 2. Change the setting

Only the provider's `logo` line changes:

```json Before: providers
"providers": {
  "gemini": { "ui": { "logo": "/static/ai/providers/gemini.svg" } }
}
```

```json After: providers
"providers": {
  "gemini": { "ui": { "logo": "/static/ai/providers/gemini-new.svg" } }
}
```

The path starts with `/static/` and matches the file name exactly.

### Step 3. Save, reload and check

Click **Reload configuration**. If you kept the same file name, refresh the browser with Ctrl+F5. The new logo shows in **Providers**, in the group header under **Available models**, and as the small logo above each model name.

### Expected result

The provider logo changed. The model logos did **not**. They are set separately in `models`.

### Checklist

**Check before saving**

- [ ] The file is in `static/ai/providers/`
- [ ] The path starts with `/static/` and matches the file name
- [ ] Only `providers` was edited, not `models`

**After saving**

- [ ] Clicked **Reload configuration**
- [ ] The new logo shows
- [ ] Model logos are unchanged

## H. Change a model logo

**Scenario:** Suppose I want Gemini X Flash to show a different logo.

**What you will change**

- The logo file in `static/ai/models/`
- `models` → the model → `ui` → `logo`

### Step 1. Add the file

Put the file in `static/ai/models/`, for example `gemini-x.svg`.

### Step 2. Change the setting

Only that model's `logo` line changes:

```json Before: models
"models": {
  "gemini-x-flash": { "ui": { "logo": "/static/ai/models/gemini.svg" } }
}
```

```json After: models
"models": {
  "gemini-x-flash": { "ui": { "logo": "/static/ai/models/gemini-x.svg" } }
}
```

### Step 3. Save, reload and check

Click **Reload configuration**. Refresh with Ctrl+F5 if you kept the same file name. The new logo shows next to the model name on its card and on the feature cards that use it.

### Expected result

The model logo changed. The provider logo did **not**.

A model with no logo shows none. It never borrows its provider's. This separation is on purpose: the same model can be served by different providers, and each provider keeps its own logo.

### Checklist

**Check before saving**

- [ ] The file is in `static/ai/models/`
- [ ] The path starts with `/static/` and matches the file name
- [ ] Only `models` was edited, not `providers`

**After saving**

- [ ] Clicked **Reload configuration**
- [ ] The new model logo shows
- [ ] The provider logo is unchanged

## I. Change an API key

**Scenario:** Suppose my provider gave me a new key and I want SmartAIExam to use it.

> **Note:** This is also how you add a key that does not exist yet. If a new model is greyed out with "API key for this provider is not configured.", this is the fix: set the provider's key, then restart.

**What you will change**

- The server's environment settings: the value of one named setting
- Not the file

### Step 1. Find the setting's name

Open `config/ai_models.json`. The name is written next to `key_env`, under the provider in `providers`. That is the provider's normal key, used by every model of that provider.

A feature can also have its own key for a provider, under `credential_env` in `assignments` (optional, rare). A feature uses the first key that is actually set: its own, then the model's, then the provider's. To change the key of one feature only, change the setting named there. To change the key for everything, change the one next to `key_env`.

```json Example: providers
"providers": {
  "exampleco": {
    "api": { "base_url": "https://api.exampleco.example/v1", "key_env": "EXAMPLECO_API_KEY" }
  }
}
```

Here the setting is named `EXAMPLECO_API_KEY`. The file holds only this **name**.

### Step 2. Change the value where the key is stored

- **On your computer:** edit the line in the `.env` file in the project's main folder.
- **On a hosting service:** edit the setting in the service's Environment settings.

```env Copy this
EXAMPLECO_API_KEY=paste-the-new-key-here
```

### Step 3. Restart the application

Keys are read when the application starts. **Reload configuration** does not re-read them. Some hosts restart the service when you save an environment change. If yours does not, restart it yourself.

### Step 4. Check

Open **Admin → AI Configuration**. The provider and its models show the key as configured. The page cannot tell you *which* key is in use, so run a real feature test. If the old key has been revoked, a working test proves the new one is in use.

### Expected result

The provider shows as configured and its features work.

> **Warning:** never put an API key in the JSON file, in frontend code, on the Admin page, in Git or in logs. If a key was ever exposed, revoke it at the provider and create a new one.

### Checklist

**Check before saving**

- [ ] The setting name is exactly the one in `key_env` or `credential_env`
- [ ] The key is not in `ai_models.json`
- [ ] No quotes or spaces around the key

**After saving**

- [ ] Restarted the application
- [ ] The provider shows the key as configured
- [ ] A real test of a feature using this provider works

## J. Change rate limits

**Scenario:** Suppose my provider changed its limits and I want the Admin page to show the new numbers.

**What you will change**

- `rate_limits` → the entry named `provider/model`

### Two kinds of limits

| Kind | What it is | Where it is stored |
|---|---|---|
| **Model size limits** | How big the model is: how much it can read, how long a reply can be | `offerings` → `limits` |
| **Rate limits** | How much you may use, per plan, account or project | `rate_limits` |

SmartAIExam does **not** read your live limits from the provider and does not enforce any limit. `rate_limits` holds reference numbers **you** type in from the provider's documentation or dashboard. The provider enforces the real limits.

### Step 1. Get the real numbers

Use the provider's rate-limit documentation or your account dashboard. Do not guess. If you cannot find a number, leave it out.

### Step 2. Edit the entry

Only the numbers (and `as_of`) change:

```json Before: rate_limits
"rate_limits": {
  "gemini/gemini-x-flash": {
    "scope": "project",
    "source": "Provider dashboard",
    "as_of": "2026-09-20",
    "limits": { "rpm": 15, "rpd": 500, "tpm": 250000 }
  }
}
```

```json After: rate_limits
"rate_limits": {
  "gemini/gemini-x-flash": {
    "tier": "Paid plan",
    "scope": "project",
    "source": "Provider dashboard",
    "as_of": "2026-10-01",
    "limits": { "rpm": 150, "rpd": 10000, "tpm": 1000000 }
  }
}
```

| Field | Means |
|---|---|
| `rpm` | Requests per minute |
| `rpd` | Requests per day |
| `tpm` | Tokens per minute |
| `tpd` | Tokens per day |
| `input_tpm` | Input tokens per minute |
| `output_tpm` | Output tokens per minute |
| `concurrent_requests` | Requests allowed at the same time |

A value is a number, or `"unlimited"`. Add only the fields your provider gives. A missing field is fine, and the page shows only what is there.

Around the numbers you can record `tier` (the plan), `scope` (organization or project), `source`, `as_of` and `note`.

### Step 3. Save, reload and check

Click **Reload configuration**. Click the model card and open **Model details**. **Rate limits** shows your numbers. The card also shows short chips such as `150 RPM`.

### Expected result

The page shows the new numbers. A model with no entry says "not published".

### Checklist

**Check before saving**

- [ ] The numbers come from the provider, not a guess
- [ ] The entry name is exactly `provider/model`
- [ ] Values are numbers (or `"unlimited"`)
- [ ] `as_of` is updated

**After saving**

- [ ] Clicked **Reload configuration**
- [ ] **Model details** shows the new numbers

## K. Change what a model can do

**Scenario:** Suppose Gemini X Flash also supports a capability I had not listed, or I listed one it does not support.

**What you will change**

- `models` → the model → `capabilities`

### What each capability means

| Capability | Means | Used by |
|---|---|---|
| `text` | Reads and writes text | Assistant Chat Replies, Chat Title Generation, Explanation (text questions), Generation from text |
| `vision` | Reads images | Explanation (image questions), Generation from scanned PDFs |
| `pdf` | Reads PDF files directly | Generation from scanned PDFs |
| `structured_output` | Can return structured (JSON) answers | No feature requires it. Used when a feature asks for JSON |
| `embedding` | Produces vector embeddings | No feature uses embeddings today |

### Step 1. Check the provider's documentation

Confirm what the model really supports. Do not switch a capability on because a feature needs it.

### Step 2. Edit the list

```json Before: models
"models": {
  "gemini-x-flash": { "capabilities": ["text"] }
}
```

```json After: models
"models": {
  "gemini-x-flash": { "capabilities": ["text", "vision", "pdf", "structured_output"] }
}
```

Only these names are valid. An unknown name makes the model unusable, with an error.

If one provider offers fewer abilities than the model normally has, put a `capabilities` list on **that provider's offering** instead. It replaces the model's list for that provider only.

### Step 3. Save, reload and check

Click **Reload configuration**. The model card shows the new ability chips, and **Change Model** on the relevant features now lists the model.

### Expected result

The model is offered exactly for the features whose needs its capabilities meet.

> **Warning:** capabilities must match reality. If you mark a capability the model does not support, the model is still offered for that feature and then **fails** when the feature uses it. For example, an image sent to a text-only model returns an error from the provider. The Admin page cannot detect this. If you leave out a capability the model does support, it is simply not offered for those features.

### Checklist

**Check before saving**

- [ ] Each capability is documented by the provider
- [ ] Only valid capability names are used
- [ ] JSON is valid

**After saving**

- [ ] Clicked **Reload configuration**
- [ ] The card shows the right abilities
- [ ] A real test of a feature using the new ability works

## L. Add another model to a provider that already exists

**Scenario:** Suppose Groq is already in my file, and I only want to add one more Groq model.

You do **not** add a provider, an API key or a logo for the provider. That is what makes this different from tutorial C.

| Section | What to do |
|---|---|
| `models` | Add the new model |
| `offerings` | Add it under the existing provider's key |
| `rate_limits` | Add an entry if you know the limits (optional) |
| `assignments` | Only if a feature should use it by default |
| `providers` | Do not change |

Follow **Steps 1 to 4** of [tutorial A](#a-add-a-new-model-to-an-existing-provider), using your provider's key (for example `groq`) instead of `gemini`. Nothing else is touched.

## M. Switch a feature from one provider to another

**Scenario:** Suppose **Explanation (text questions)** uses GPT-OSS 120B from Groq now (Provider A, Model A), and I want it to use Gemini X Flash from Google Gemini (Provider B, Model B).

**What you will change**

- The feature's model: with **Change Model** (or the `model` line in `assignments`)
- Possibly a key for Provider B (Step 3)

Nothing in `providers`, `models` or `offerings` changes.

### Step 1. Check Model B can do the job

The feature needs the abilities in the features table near the top. **Explanation (text questions)** needs text. Model B must be listed in **Change Model** for that feature.

### Step 2. Check Provider B's key

Click **Change Model** on the feature. If Model B is greyed out with "API key for this provider is not configured.", Provider B's key is not set yet. Go to Step 3. If it can be selected, skip to Step 4.

### Step 3. Set Provider B's key

Set Provider B's key setting (its `key_env` in `providers`) and restart the application. Tutorial I shows how. Every feature can then use Provider B. Nothing in the file changes.

**Optional: a key for only this feature.** If this feature should have its own usage quota with Provider B, add a `credential_env` line for Provider B to that feature in `assignments`, then set that named setting and restart. The feature uses that setting when it is set, and Provider B's normal key when it is not.

```json After: assignments
"assignments": {
  "explanation_text": { "credential_env": { "gemini": "EXAMPLE_GEMINI_KEY_NAME" } }
}
```

Keep the existing lines of that feature. This only adds Gemini next to what is already there. `EXAMPLE_GEMINI_KEY_NAME` is a placeholder: choose your own name and set it to the key in the environment.

### Step 4. Switch the model

Click **Change Model**, choose **Gemini X Flash**, and click **Save Configuration**. Or change the default in the file:

```json After: assignments
"assignments": {
  "explanation_text": { "model": "gemini/gemini-x-flash" }
}
```

### Step 5. Check and test

The card shows **Gemini X Flash** under **Google Gemini**. Request an explanation for a wrong answer to test it.

### Step 6. Go back if needed

Click **Change Model**, then **Reset to default**.

### Expected result

Only **Explanation (text questions)** moved to Provider B. The other features still use their own models and keys.

> **Tip:** moving a feature to another provider moves its usage to Provider B's account, and Provider B's limits apply.

### Checklist

**Check before saving**

- [ ] Model B is offered for this feature
- [ ] Provider B's key is set
- [ ] Only the `model` (and if needed `credential_env`) lines changed

**After saving**

- [ ] Restarted the application if a key was added
- [ ] The card shows Provider B and Model B
- [ ] The other features are unchanged
- [ ] A real test works

## N. Troubleshooting

Each problem has the same four parts: symptom, cause, fix and how to verify.

### My new model is greyed out: "API key for this provider is not configured."

- **Symptom:** You added a model and followed the steps. In **Change Model** it is listed but greyed out, with "API key for this provider is not configured." under it. It may be selectable for some features and greyed out for others.
- **Cause:** The JSON is fine. The provider's API key is not set on the server. A model uses its provider's key, or, if a feature has its own key for that provider and it is set, that one.
- **Fix:** Follow Step 5 of [tutorial A](#a-add-a-new-model-to-an-existing-provider): set the provider's key in the environment (tutorial I) and restart the application.
- **Verify:** The provider's group says **API key configured**, and **Change Model** no longer greys the model out.

### The model does not appear in Admin AI Configuration

- **Symptom:** You added a model but cannot find it under **Available models**.
- **Cause:** The file was not saved or deployed to the server that runs the site; the file has a JSON mistake (the site keeps the old configuration); you did not click **Reload configuration**; or the model has no offering.
- **Fix:** Check the file on the live server. Look for a message at the top of the page. Click **Reload configuration**. Make sure the model has an entry under `offerings` (tutorial A, Step 3).
- **Verify:** The model card appears under its provider, with no message at the top.

### The provider appears but its model does not

- **Symptom:** The provider is listed, but its model is missing.
- **Cause:** There is no offering for it, or the model key in `offerings` does not match the key in `models` exactly (a typo). The page may say “unknown model” or “is not offered by any provider”.
- **Fix:** In `offerings`, under the provider, use a key identical to the one in `models`.
- **Verify:** Reload. The model card appears and the message is gone. Do not create a second provider or model.

### The model appears but cannot be selected for a feature

- **Symptom:** In **Change Model** the model is greyed out, or it is not listed at all.
- **Cause:** Greyed out means the provider's API key is not set. **Not listed** means the model is disabled or lacks an ability the feature needs.
- **Fix:** Read the reason under the greyed-out model. For the key, follow Step 5 of [tutorial A](#a-add-a-new-model-to-an-existing-provider). For a disabled model, tutorial E. For abilities, tutorial K.
- **Verify:** Reopen **Change Model**. The model can now be selected.

### API key error

- **Symptom:** A feature card shows **Attention** with “API key for this provider is not configured.”
- **Cause:** No key is set for this provider. The feature uses its own key if it has one and it is set, otherwise the provider's `key_env` setting. That setting is missing or empty, its name differs from the file, or the application was not restarted after you added it.
- **Fix:** Set the provider's key in the environment (tutorial I) and restart the application.
- **Verify:** The provider shows the key as configured, and the feature works.

### Unsupported capability

- **Symptom:** The model is offered for a feature, but using the feature fails with a provider error. Or a feature does not offer a model you expected.
- **Cause:** `capabilities` do not match what the model supports. Too many capabilities cause failures. Too few hide the model from features.
- **Fix:** Correct `capabilities` from the provider's documentation (tutorial K). The page can also say “does not support vision” or “does not support PDF/document input”.
- **Verify:** Reload, then run a real test of the feature.

### Invalid JSON

- **Symptom:** A message at the top says the file “is not valid JSON” and gives a line, and your change does not appear.
- **Cause:** A missing or extra comma, a missing quote, or a comment.
- **Fix:** Open the file at that line and look just above it. Paste the file into a JSON validator.
- **Verify:** Click **Reload configuration**. The message disappears. Do not restart the server while the file is broken. It has no last good copy to fall back on.

### The model is configured but the feature still uses the old model

- **Symptom:** You changed the file, but the feature card shows the old model.
- **Cause:** An Admin choice exists, and it wins over the file. Or you edited `models` when the decision is made in `assignments`. The environment settings do not matter here: they never choose a model.
- **Fix:** Click **Change Model** on the feature. If the card says **Source: Admin selection**, click **Reset to default**. Wait about 30 seconds if you have several servers.
- **Verify:** The card shows the model from the file, with **Source: Registry default**.

### The model is disabled

- **Symptom:** A feature card shows **Attention** with “Selected model is disabled.”
- **Cause:** The offering has `"enabled": false` (or its provider does).
- **Fix:** Set `"enabled": true`, or move the feature to another model (tutorials E and D).
- **Verify:** Reload. The card is **Active**.

### The provider's protocol is unsupported

- **Symptom:** The top of the page says “no adapter is installed” for a provider.
- **Cause:** The `protocol` name is misspelled, or the provider uses an API style the application does not have yet.
- **Fix:** Check the spelling against the three supported names (tutorial C, Step 1). If the provider really uses another protocol, **THIS REQUIRES CODE CHANGES**. A developer must add an adapter.
- **Verify:** After the fix, its models appear with no message.

### The Admin page does not reflect my changes

- **Symptom:** You saved the file, but the page looks the same.
- **Cause:** The change is in a different copy of the file than the server reads; you did not reload; the browser page is old; or another server has not refreshed yet.
- **Fix:** Confirm you edited the live file (deploy it if you use a repository). Click **Reload configuration**. Refresh with Ctrl+F5. Wait about 30 seconds.
- **Verify:** The page shows your change.

### The application needs a restart or reload

- **Symptom:** You are not sure which of the two you need.
- **Cause and fix:**

| You changed | You need |
|---|---|
| The file (models, providers, offerings, limits, logos) | **Reload configuration**. No restart |
| A choice made with **Change Model** | Nothing |
| An API key or environment setting | Restart the application |
| Application code (a new adapter) | Restart or redeploy |

- **Verify:** After the right step, the Admin page shows the change.

### The logo is not appearing

- **Symptom:** A provider shows an initials badge, or a model shows no logo.
- **Cause:** The path is wrong, the file is not in the `static` folder, or the browser cached the old one. The top of the page says “Logo file for” and names the file if it is missing.
- **Fix:** Make sure the path starts with `/static/` and the file really is there. Press Ctrl+F5. Remember that provider and model logos are separate settings (tutorials G and H).
- **Verify:** Reload configuration. The logo shows and the message is gone.

### The provider works but requests for one model fail

- **Symptom:** Other models work, but this one fails.
- **Cause:** The `model_id` is wrong or the provider has retired the model, you hit the provider's rate limit, the account has no access to that model, or the model cannot handle what the feature sends.
- **Fix:** Compare `model_id` with the provider's current model list. Check the provider's rate limits. The server log records the provider's reply. Change only that offering. Do not touch the provider or key.
- **Verify:** A real test of the feature works.

### A model is slow, times out, or its replies look like it is "thinking" out loud

- **Symptom:** After you switched a feature to a new model it takes much longer to answer than before, sometimes ends in "AI provider request timed out while waiting for a response.", or the reply starts with the model's own planning notes.
- **Cause:** Some models reason before they answer (this is often called "thinking"). That thinking takes seconds, counts against the model's token limits, and can be switched on by default. The application never shows the thinking as part of a reply, but the waiting time and the tokens are still spent. Raising the timeout only hides this. How much a model thinks is model-specific, so it is set on that model's **offering**, and only there.
- **Fix:** In `offerings`, on the entry of that model under its provider, add an `options` block with the provider's own setting for thinking. The setting name and the values it accepts are different for every model: copy them from the provider's documentation for **that** model. If the provider answers with an error that names the setting, the model does not accept that value. Only this offering changes. Other models and other features are not touched.

```json Example: offerings
"offerings": {
  "gemini": {
    "gemini-x-flash": {
      "options": { "extra_generation_config": { "thinkingConfig": { "thinkingLevel": "minimal" } } }
    }
  }
}
```

- **Verify:** Click **Reload configuration**, then use the feature. The answer arrives in a few seconds and starts with the answer itself. The server log has one line per AI request with the provider, the model and how long the provider took.

## Quick reference

| What I want to do | Where I change it |
|---|---|
| Add a new model | `models` and `offerings` (and `rate_limits` if known) |
| Add an existing model through another provider | `offerings` (and `providers` if the provider is new) |
| Add another model to an existing provider | `models` and `offerings` |
| Add a provider | `providers`, its `offerings`, and its key in the environment |
| Change which model a feature uses | Admin: **Change Model** (or `assignments`, then the feature, then `model`) |
| Put a feature back to its default | Admin: **Change Model**, then **Reset to default** |
| Choose a model in the environment settings | Not possible. The environment settings only hold API keys |
| Change a provider logo | `providers`, then the provider, then `ui`, then `logo` |
| Change a model logo | `models`, then the model, then `ui`, then `logo` |
| Change an API key | The server's environment settings, then restart |
| Change rate limits | `rate_limits`, then `provider/model` |
| Change size limits (context, output) | `offerings`, then `limits` |
| Change what a model can do | `models`, then the model, then `capabilities` |
| Switch a model off for now | `offerings`, then the model, then `enabled` set to false |
| Switch a whole provider off | `providers`, then the provider, then `enabled` set to false |
| Remove a model for good | Move features, then `assignments`, then `offerings`, then `rate_limits`, then `models`, then the logo file |
| Rename a model or provider on the page | `display_name` in `models` or `providers` |

## For developers

**THIS REQUIRES CODE CHANGES:** adding a provider whose API is not `openai_compatible`, `gemini` or `anthropic`.

1. Add a new file in the `adapters` folder of the AI service code. Copy `openai_compatible.py`, the shortest one.
2. Give it a `protocol` name, register it, and write how to build the address, headers and request, and how to read the reply.
3. Import it in the adapters package.
4. Add the provider to `ai_models.json` with that protocol name (tutorial C).
5. Run the tests: `python -m unittest tests.test_ai_layer -v`.

Small differences in an almost-standard API can often be handled in the file with `options` on the provider or an offering:

| Protocol | Options |
|---|---|
| `openai_compatible` | `max_tokens_field`, `omit_params`, `extra_body`, `low_reasoning_effort` |
| `gemini` | `omit_params`, `extra_generation_config`, `extra_body` |
| `anthropic` | `anthropic_version`, `default_max_tokens`, `omit_params`, `extra_body` |

```json After: providers
"providers": {
  "exampleco": { "options": { "max_tokens_field": "max_completion_tokens" } }
}
```

`low_reasoning_effort` is for a model that thinks before it answers (for example `"low_reasoning_effort": "low"`; the value is whatever that model's provider accepts). Nothing changes for normal requests. Only when a reply is cut off because the model spent its whole output budget on hidden thinking and wrote nothing, the explanation feature asks once more, this time with that setting. A model without the option is never sent it.

Every JSON example in this guide is checked by the automated tests, together with the names and messages it mentions. The Admin **Guide** button loads this file. Keep it the only guide, and keep keys, real environment-variable names and real addresses out of it.
