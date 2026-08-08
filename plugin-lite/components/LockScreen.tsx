/*
 * Vencord, a Discord client mod
 * Copyright (c) 2026 Vendicated and contributors
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

import ErrorBoundary from "@components/ErrorBoundary";
import { formatDurationVerbose } from "@utils/text";
import type { Channel } from "@vencord/discord-types";
import { findComponentByCodeLazy } from "@webpack";
import { Parser, SnowflakeUtils, Text, Timestamp, Tooltip } from "@webpack/common";

import { cl, getDisplayName } from "..";

const enum ChannelTypes {
    GUILD_TEXT = 0,
    GUILD_VOICE = 2,
    GUILD_ANNOUNCEMENT = 5,
    GUILD_STAGE_VOICE = 13,
    GUILD_FORUM = 15
}

const enum ChannelFlags {
    REQUIRE_TAG = 1 << 4
}

const TagComponent = findComponentByCodeLazy("#{intl::FORUM_TAG_A11Y_FILTER_BY_TAG}");

const ChannelTypesToChannelNames = {
    [ChannelTypes.GUILD_TEXT]: "text",
    [ChannelTypes.GUILD_ANNOUNCEMENT]: "announcement",
    [ChannelTypes.GUILD_FORUM]: "forum",
    [ChannelTypes.GUILD_VOICE]: "voice",
    [ChannelTypes.GUILD_STAGE_VOICE]: "stage"
};

// Icon from the modal when clicking a message link you don't have access to view
const HiddenChannelLogo = "/assets/433e3ec4319a9d11b0cbe39342614982.svg";

function LockScreen({ channel }: { channel: Channel; }) {
    const {
        type,
        topic,
        lastMessageId,
        lastPinTimestamp,
        rateLimitPerUser,
        availableTags,
        id: channelId
    } = channel;

    return (
        <div className={cl("container")}>
            <img className={cl("logo")} src={HiddenChannelLogo} />

            <div className={cl("heading-container")}>
                <Text variant="heading-xxl/bold">
                    {getDisplayName(channel)} — hidden {ChannelTypesToChannelNames[type]} channel
                </Text>
                {channel.isNSFW() &&
                    <Tooltip text="NSFW">
                        {({ onMouseLeave, onMouseEnter }) => (
                            <svg
                                onMouseLeave={onMouseLeave}
                                onMouseEnter={onMouseEnter}
                                className={cl("heading-nsfw-icon")}
                                width="32"
                                height="32"
                                viewBox="0 0 48 48"
                                aria-hidden={true}
                                role="img"
                            >
                                <path fill="currentColor" d="M.7 43.05 24 2.85l23.3 40.2Zm23.55-6.25q.75 0 1.275-.525.525-.525.525-1.275 0-.75-.525-1.3t-1.275-.55q-.8 0-1.325.55-.525.55-.525 1.3t.55 1.275q.55.525 1.3.525Zm-1.85-6.1h3.65V19.4H22.4Z" />
                            </svg>
                        )}
                    </Tooltip>
                }
            </div>

            {(!channel.isGuildVoice() && !channel.isGuildStageVoice()) && (
                <Text variant="text-lg/normal">
                    You can not see the {channel.isForumChannel() ? "posts" : "messages"} of this channel.
                    {channel.isForumChannel() && topic && topic.length > 0 && " However you may see its guidelines:"}
                </Text>
            )}

            {channel.isForumChannel() && topic && topic.length > 0 && (
                <div className={cl("topic-container")}>
                    {Parser.parseTopic(topic, false, { channelId })}
                </div>
            )}

            {lastMessageId &&
                <Text variant="text-md/normal">
                    Last {channel.isForumChannel() ? "post" : "message"} created:
                    <Timestamp timestamp={new Date(SnowflakeUtils.extractTimestamp(lastMessageId))} />
                </Text>
            }
            {lastPinTimestamp &&
                <Text variant="text-md/normal">Last message pin: <Timestamp timestamp={new Date(lastPinTimestamp)} /></Text>
            }
            {(rateLimitPerUser ?? 0) > 0 &&
                <Text variant="text-md/normal">Slowmode: {formatDurationVerbose(rateLimitPerUser!, "seconds")}</Text>
            }
            {channel.hasFlag(ChannelFlags.REQUIRE_TAG) &&
                <Text variant="text-md/normal">Posts on this forum require a tag to be set.</Text>
            }
            {availableTags && availableTags.length > 0 &&
                <div className={cl("tags-container")}>
                    <Text variant="text-lg/bold">Available tags:</Text>
                    <div className={cl("tags")}>
                        {availableTags.map(tag => <TagComponent tag={tag} key={tag.id} />)}
                    </div>
                </div>
            }
        </div>
    );
}

export default ErrorBoundary.wrap(LockScreen);
