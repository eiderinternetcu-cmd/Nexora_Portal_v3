"""
Reason codes for entitlement / playback authorization decisions.

Shared by EntitlementService and PlaybackAuthorizationService so that deny
responses carry a stable, machine-readable code (never a secret).
"""
from enum import Enum


class ReasonCode(str, Enum):
    ALLOW = "ALLOW"

    SUBSCRIBER_NOT_FOUND = "SUBSCRIBER_NOT_FOUND"
    SUBSCRIBER_SUSPENDED = "SUBSCRIBER_SUSPENDED"
    SUBSCRIBER_DISABLED = "SUBSCRIBER_DISABLED"

    SUBSCRIPTION_NOT_FOUND = "SUBSCRIPTION_NOT_FOUND"
    SUBSCRIPTION_EXPIRED = "SUBSCRIPTION_EXPIRED"
    SUBSCRIPTION_CANCELLED = "SUBSCRIPTION_CANCELLED"
    SUBSCRIPTION_SUSPENDED = "SUBSCRIPTION_SUSPENDED"

    PLAN_INACTIVE = "PLAN_INACTIVE"

    DEVICE_NOT_REGISTERED = "DEVICE_NOT_REGISTERED"
    DEVICE_BLOCKED = "DEVICE_BLOCKED"

    CHANNEL_NOT_FOUND = "CHANNEL_NOT_FOUND"
    CHANNEL_INACTIVE = "CHANNEL_INACTIVE"
    CHANNEL_NOT_INCLUDED = "CHANNEL_NOT_INCLUDED"

    # Parental control (NX-PARENTAL). Distinct codes on purpose: the client has
    # to tell "ask for the PIN" apart from "there is no PIN to ask for yet",
    # otherwise the second case becomes an unanswerable prompt.
    PARENTAL_PIN_NOT_SET = "PARENTAL_PIN_NOT_SET"   # censored channel, subscriber has no PIN
    PARENTAL_PIN_REQUIRED = "PARENTAL_PIN_REQUIRED" # censored channel, no unlock in force
    PARENTAL_PIN_INVALID = "PARENTAL_PIN_INVALID"   # wrong PIN presented to /parental/*
    PARENTAL_PIN_LOCKED = "PARENTAL_PIN_LOCKED"     # too many wrong PINs — entry blocked
