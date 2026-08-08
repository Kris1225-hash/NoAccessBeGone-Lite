/*
 * Vencord, a Discord client mod
 * Copyright (c) 2026 Vendicated and contributors
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

import "./style.css";

import { get as getStore, set as setStore } from "@api/DataStore";
import { definePluginSettings } from "@api/Settings";
import ErrorBoundary from "@components/ErrorBoundary";
import { classNameFactory } from "@utils/css";
import { Logger } from "@utils/Logger";
import definePlugin, { OptionType } from "@utils/types";
import type { Channel } from "@vencord/discord-types";
import { findCssClassesLazy } from "@webpack";
import { ChannelStore, FluxDispatcher, GuildStore, PermissionsBits, PermissionStore } from "@webpack/common";

import LockScreen from "./components/LockScreen";

const logger = new Logger("NoAccessBeGoneLite");

const OVERRIDES_KEY = "noAccessBeGoneLite_nameOverrides";
const OBFUSCATED_NAME = "___hidden___";

const nameOverrides = new Map<string, string>();

export function getDisplayName(channel: Channel | null | undefined) {
    if (channel == null) return "";
    return nameOverrides.get(channel.id) ?? channel.name;
}

async function loadOverrides() {
    try {
        const stored = await getStore(OVERRIDES_KEY) as Record<string, string> | undefined;
        if (!stored) return;
        for (const [id, name] of Object.entries(stored)) nameOverrides.set(id, name);
        logger.info(`loaded ${nameOverrides.size} cached channel names`);
    } catch (e) {
        logger.error("loadOverrides failed:", e);
    }
}

async function persistOverrides() {
    try {
        await setStore(OVERRIDES_KEY, Object.fromEntries(nameOverrides));
    } catch (e) {
        logger.error("persistOverrides failed:", e);
    }
}

export const cl = classNameFactory("nab-");

const ChannelListClasses = findCssClassesLazy("modeSelected", "modeMuted", "unread", "icon");

export const settings = definePluginSettings({
    queryUrl: {
        description: "Name database query endpoint",
        type: OptionType.STRING,
        default: "https://nab.enby.fish/nab/request",
        restartNeeded: true
    },
    queryMinutes: {
        description: "How often to re-query uncached servers (minutes)",
        type: OptionType.NUMBER,
        default: 30,
        restartNeeded: true
    },
    hideUnreads: {
        description: "Hide unread indicators on hidden channels",
        type: OptionType.BOOLEAN,
        default: true,
        restartNeeded: true
    }
});

function isUncategorized(objChannel: { channel: Channel; comparator: number; }) {
    return objChannel.channel.id === "null" && objChannel.channel.name === "Uncategorized" && objChannel.comparator === -1;
}

export default definePlugin({
    name: "NoAccessBeGoneLite",
    description: "Show hidden channels; display names from the community name database",
    tags: ["Servers", "Utility"],
    authors: [{ name: "matthew", id: 0n }],
    settings,

    patches: [
        // The obfuscation flag is what makes every name formatter return the "No Access" label
        // instead of channel.name. If we have the real name, report the channel as not obfuscated.
        {
            find: /isObfuscated\(\)\{return this\.hasFlag/,
            replacement: {
                match: /isObfuscated\(\)\{return this\.hasFlag\((\i\.\i)\.OBFUSCATED\)\}/,
                replace: "isObfuscated(){if($self.hasRealName(this))return false;return this.hasFlag($1.OBFUSCATED)}"
            }
        },
        // Belt & suspenders: in the name formatter itself, skip the obfuscated label if we have the real name
        {
            find: '"/YzI63"',
            replacement: {
                match: /if\((\i)\.isObfuscated\(\)\)return (\i)\.intl\.string\(\2\.t\["\/YzI63"\]\)/,
                replace: "if($1.isObfuscated()&&!$self.hasRealName($1))return $2.intl.string($2.t[\"/YzI63\"])"
            }
        },
        {
            // RenderLevel defines if a channel is hidden, collapsed in category, visible, etc
            find: '"placeholder-channel-id"',
            replacement: [
                // Replace the special no-permission logic: hidden channels always render at the
                // visible level instead of renderLevel 1 (which the list filters out entirely).
                {
                    match: /if\(!\i\.\i\.can\(\i\.\i\.VIEW_CHANNEL.+?{if\(this\.id===\i\).+?threadIds:\[\]}}/,
                    replace: "if($self.isHiddenChannel(this.record))return{renderLevel:4,threadIds:[]};"
                },
                // Do not check for unreads when selecting the render level if the channel is hidden
                {
                    match: /(?<=&&)(?=!\i\.\i\.hasUnread\(this\.record\.id\))/,
                    replace: "$self.isHiddenChannel(this.record)||"
                },
                // Make channels we dont have access to be the same level as normal ones
                {
                    match: /(this\.record\)\?{renderLevel:(.+?),threadIds.+?renderLevel:).+?(?=,threadIds)/g,
                    replace: (_, rest, defaultRenderLevel) => `${rest}${defaultRenderLevel}`
                },
                // Remove permission checking for getRenderLevel function
                {
                    match: /(getRenderLevel\(\i\){.+?return)!\i\.\i\.can\(\i\.\i\.VIEW_CHANNEL,this\.record\)\|\|/,
                    replace: (_, rest) => `${rest} `
                }
            ]
        },
        // Prevent Discord from trying to connect to hidden voice channels
        {
            find: "VoiceChannel, transitionTo: Channel does not have a guildId",
            replacement: [
                {
                    match: /(?<=getIgnoredUsersForVoiceChannel\((\i)\.id\)[^;]{0,300}?;return\()/,
                    replace: (_, channel) => `!$self.isHiddenChannel(${channel})&&`
                },
                {
                    match: /(?=\|\|\i\.\i\.selectVoiceChannel\((\i)\.id\))/,
                    replace: (_, channel) => `||$self.isHiddenChannel(${channel})`
                },
                {
                    match: /!__OVERLAY__&&\((?<=selectVoiceChannel\((\i)\.id\).+?)/,
                    replace: (m, channel) => `${m}$self.isHiddenChannel(${channel},true)||`
                }
            ]
        },
        // Prevent Discord from trying to connect to hidden stage channels
        {
            find: ".AUDIENCE),{isSubscriptionGated",
            replacement: {
                match: /(\i)\.isRoleSubscriptionTemplatePreviewChannel\(\)/,
                replace: (m, channel) => `${m}||$self.isHiddenChannel(${channel})`
            }
        },
        // Render null instead of the buttons if the channel is hidden
        {
            find: 'tutorialId:"instant-invite"',
            replacement: [
                ...[
                    "renderEditButton",
                    "renderInviteButton",
                ].map(func => ({
                    match: new RegExp(`(?<=${func}\\(\\){)`, "g"),
                    replace: "if($self.isHiddenChannel(this?.props?.channel))return null;"
                }))
            ]
        },
        {
            find: "VoiceChannel.renderPopout: There must always be something to render",
            all: true,
            replacement: {
                match: /(?<=renderOpenChatButton(?:",|=)\(\)=>{)/,
                replace: "if($self.isHiddenChannel(this?.props?.channel))return null;"
            }
        },
        // Lock icon for hidden channels
        {
            find: "#{intl::CHANNEL_TOOLTIP_DIRECTORY}",
            replacement: {
                match: /(?<=(\i)\.isNSFW\(\);)switch\(\i\.type\).{0,15}\.GUILD_ANNOUNCEMENT/,
                replace: (m, channel) => `if($self.isHiddenChannel(${channel}))return $self.LockIcon;${m}`
            }
        },
        // Hide unread indicators for hidden channels
        {
            find: "UNREAD_IMPORTANT:",
            predicate: () => settings.store.hideUnreads,
            replacement: [
                {
                    match: /Children\.count.+?;(?=return\(0,\i\.jsxs?\)\(\i\.\i,{focusTarget:)(?<={channel:(\i),name:\i,muted:(\i).+?;)/,
                    replace: (m, channel, muted) => `${m}${muted}=$self.isHiddenChannel(${channel})?true:${muted};`
                },
                {
                    match: /Children\.count.+?;(?=return\(0,\i\.jsxs?\)\(\i\.\i,{focusTarget:)(?<={channel:(\i),name:\i,.+?unread:(\i).+?)/,
                    replace: (m, channel, unread) => `${m}${unread}=$self.isHiddenChannel(${channel})?false:${unread};`
                }
            ]
        },
        {
            // Hide the new version of unreads box for hidden channels
            find: '"ChannelListUnreadsStore"',
            predicate: () => settings.store.hideUnreads,
            replacement: {
                match: /(?<=\.id\)\))(?=&&\(0,\i\.\i\)\((\i)\))/,
                replace: (_, channel) => `&&!$self.isHiddenChannel(${channel})`
            }
        },
        {
            // Make the old version of unreads box not visible for hidden channels
            find: "renderBottomUnread(){",
            predicate: () => settings.store.hideUnreads,
            replacement: {
                match: /(?<=!0\))(?=&&\(0,\i\.\i\)\((\i\.record)\))/,
                replace: "&&!$self.isHiddenChannel($1)"
            }
        },
        {
            // Make the state of the old version of unreads box not include hidden channels
            find: "GUILD_EVENT)}),[",
            predicate: () => settings.store.hideUnreads,
            replacement: {
                match: /(?<=\.id\)\))(?=&&\(0,\i\.\i\)\((\i)\))/,
                replace: "&&!$self.isHiddenChannel($1)"
            }
        },
        // Only render the channel header and buttons that work when transitioning to a hidden channel
        {
            find: "Missing channel in Channel.renderHeaderToolbar",
            replacement: [
                {
                    match: /renderHeaderToolbar(?:",|=)\(\)=>{.+?case \i\.\i\.GUILD_TEXT:(?=.+?(\i\.push.{0,50}channel:(\i)},"notifications"\)\)))(?<=isLurking:(\i).+?)/,
                    replace: (m, pushNotificationButtonExpression, channel, isLurking) => `${m}if(!${isLurking}&&$self.isHiddenChannel(${channel})){${pushNotificationButtonExpression};break;}`
                },
                {
                    match: /renderHeaderToolbar(?:",|=)\(\)=>{.+?case \i\.\i\.GUILD_MEDIA:(?=.+?(\i\.push.{0,40}channel:(\i)},"notifications"\)\)))(?<=isLurking:(\i).+?)/,
                    replace: (m, pushNotificationButtonExpression, channel, isLurking) => `${m}if(!${isLurking}&&$self.isHiddenChannel(${channel})){${pushNotificationButtonExpression};break;}`
                },
                {
                    match: /(?<=renderHeaderBar(?:",|=)\(\)=>{.+?hideSearch:(\i)\.isDirectory\(\))/,
                    replace: (_, channel) => `||$self.isHiddenChannel(${channel})`
                },
                {
                    match: /(?<=renderSidebar\(\){)/,
                    replace: "if($self.isHiddenChannel(this?.props?.channel))return null;"
                },
                {
                    match: /(?<=renderChat\(\){)/,
                    replace: "if($self.isHiddenChannel(this?.props?.channel))return $self.LockScreen(this?.props?.channel);"
                }
            ]
        },
        // Avoid trying to fetch messages from hidden channels
        {
            find: '"MessageManager"',
            replacement: {
                match: /forceFetch:\i,isPreload:.+?}=\i;(?=.+?getChannel\((\i)\))/,
                replace: (m, channelId) => `${m}if($self.isHiddenChannel({channelId:${channelId}}))return;`
            }
        },
        // Make GuildChannelStore contain hidden channels
        {
            find: '"GuildChannelStore"',
            replacement: [
                {
                    match: /isChannelGated\(.+?\)(?=&&)/,
                    replace: m => `${m}&&false`
                },
                {
                    match: /(?<=getChannels\(\i)(\){.*?)return (.+?)}/,
                    replace: (_, rest, channels) => `,shouldIncludeHidden${rest}return $self.resolveGuildChannels(${channels},shouldIncludeHidden??arguments[0]==="@favorites");}`
                },
            ]
        },
        // Make the chat input bar channel list contain hidden channels
        {
            find: ",queryStaticRouteChannels(",
            replacement: [
                {
                    match: /(?<=queryChannels\(\i\){.+?getChannels\(\i)(?=\))/,
                    replace: ",true"
                },
                {
                    match: /(?<=queryChannels\(\i\){.+?\)\((\i)\.type\))(?=&&!\i\.\i\.can\()/,
                    replace: "&&!$self.isHiddenChannel($1)"
                }
            ]
        },
        // Make mentions of hidden channels work
        {
            find: "\"^/guild-stages/(\\\\d+)(?:/)?(\\\\d+)?\"",
            replacement: {
                match: /\i\.\i\.can\(\i\.\i\.VIEW_CHANNEL,\i\)/,
                replace: "true"
            },
        },
        {
            find: 'getConfig({location:"channel_mention"})',
            replacement: {
                match: /(?<=getChannel\(\i\);if\(null!=(\i)).{0,200}?return void (?=\i\.default\.selectVoiceChannel)/,
                replace: (m, channel) => `${m}!$self.isHiddenChannel(${channel})&&`
            }
        },
        // Make active now playing voice states on hidden channels
        {
            find: '"NowPlayingViewStore"',
            replacement: {
                match: /(getVoiceStateForUser.{0,150}?)&&\i\.\i\.canWithPartialContext.{0,20}VIEW_CHANNEL.+?}\)(?=\?)/,
                replace: "$1"
            }
        }
    ],

    getDisplayName(channel: Channel | null | undefined) {
        return getDisplayName(channel);
    },

    hasRealName(channel: Channel | null | undefined) {
        return channel != null && nameOverrides.has(channel.id);
    },

    isHiddenChannel(channel: Channel & { channelId?: string; }, checkConnect = false) {
        return isHiddenChannelImpl(channel, checkConnect);
    },

    resolveGuildChannels(channels: Record<string | number, Array<{ channel: Channel; comparator: number; }> | string | number>, shouldIncludeHidden: boolean) {
        if (shouldIncludeHidden) return channels;

        const res = {};
        for (const [key, maybeObjChannels] of Object.entries(channels)) {
            if (!Array.isArray(maybeObjChannels)) {
                res[key] = maybeObjChannels;
                continue;
            }

            res[key] ??= [];

            for (const objChannel of maybeObjChannels) {
                if (isUncategorized(objChannel) || objChannel.channel.id === null || !this.isHiddenChannel(objChannel.channel)) res[key].push(objChannel);
            }
        }

        return res;
    },

    LockScreen: (channel: any) => <LockScreen channel={channel} />,

    LockIcon: ErrorBoundary.wrap(() => (
        <svg
            className={ChannelListClasses.icon}
            height="18"
            width="20"
            viewBox="0 0 24 24"
            aria-hidden={true}
            role="img"
        >
            <path fill="currentcolor" fillRule="evenodd" d="M17 11V7C17 4.243 14.756 2 12 2C9.242 2 7 4.243 7 7V11C5.897 11 5 11.896 5 13V20C5 21.103 5.897 22 7 22H17C18.103 22 19 21.103 19 20V13C19 11.896 18.103 11 17 11ZM12 18C11.172 18 10.5 17.328 10.5 16.5C10.5 15.672 11.172 15 12 15C12.828 15 13.5 15.672 13.5 16.5C13.5 17.328 12.828 18 12 18ZM15 11H9V7C9 5.346 10.346 4 12 4C13.654 4 15 5.346 15 7V11Z" />
        </svg>
    ), { noop: true }),

    start() {
        logger.info("armed");
        void loadOverrides().then(() => {
            applyNameOverrides();
            queryAllGuilds();
        });
        refreshTimer = setInterval(queryAllGuilds, Math.max(settings.store.queryMinutes, 5) * 60 * 1000);
    },

    stop() {
        if (refreshTimer) clearInterval(refreshTimer);
        refreshTimer = null;
    },

    flux: {
        CONNECTION_OPEN: () => {
            applyNameOverrides();
            queryAllGuilds();
        },
        GUILD_CREATE: (e: any) => {
            if (!e.guild?.id) return;
            applyNameOverrides();
            queryGuild(e.guild.id);
        },
        CHANNEL_UPDATE: (e: any) => {
            const { channel } = e;
            if (!channel || channel.name !== OBFUSCATED_NAME) return;
            if (nameOverrides.has(channel.id)) {
                applyNameOverrides();
            } else if (channel.guild_id) {
                queryGuild(channel.guild_id);
            }
        }
    }
});

let refreshTimer: ReturnType<typeof setInterval> | null = null;
const queriedGuilds = new Set<string>();

function isHiddenChannelImpl(channel: Channel & { channelId?: string; }, checkConnect = false) {
    try {
        if (channel == null || Object.hasOwn(channel, "channelId") && channel.channelId == null) return false;

        if (channel.channelId != null) channel = ChannelStore.getChannel(channel.channelId);
        if (channel == null || channel.isDM() || channel.isGroupDM() || channel.isMultiUserDM()) return false;
        if (["browse", "customize", "guide"].includes(channel.id)) return false;

        return !PermissionStore.can(PermissionsBits.VIEW_CHANNEL, channel) || checkConnect && !PermissionStore.can(PermissionsBits.CONNECT, channel);
    } catch (e) {
        logger.error("[NoAccessBeGoneLite#isHiddenChannel]: ", e);
        return false;
    }
}

function getGuildChannels(guildId: string) {
    const store = ChannelStore as any;
    const candidates = [
        store.getChannels?.(guildId),
        store.getGuildChannels?.(guildId),
        store.getMutableGuildChannelsForGuild?.(guildId)
    ];

    const out: Channel[] = [];
    for (const v of candidates) {
        if (!v) continue;
        if (Array.isArray(v)) out.push(...v.map((x: any) => x.channel ?? x));
        else out.push(...Object.values(v).map((x: any) => x.channel ?? x));
    }
    return [...new Map(out.map(c => [c.id, c])).values()];
}

function queryAllGuilds() {
    for (const guild of Object.values(GuildStore.getGuilds())) {
        const channels = getGuildChannels(guild.id);
        if (channels.some(c => isHiddenChannelImpl(c) && !nameOverrides.has(c.id))) {
            queryGuild(guild.id);
        }
    }
}

async function queryGuild(guildId: string) {
    if (queriedGuilds.has(guildId)) return;
    queriedGuilds.add(guildId);

    try {
        const res = await fetch(settings.store.queryUrl, {
            method: "PUT",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ guild: guildId })
        });
        if (!res.ok) return;

        const data = await res.json() as { status?: string; names?: Array<{ c: string; n: string; }> };
        if (data.status !== "found" || !data.names?.length) return;

        let changed = false;
        for (const e of data.names) {
            if (e.n && e.n !== OBFUSCATED_NAME && !nameOverrides.has(e.c)) {
                nameOverrides.set(e.c, e.n);
                changed = true;
            }
        }
        if (changed) {
            applyNameOverrides();
            void persistOverrides();
            logger.info(`got ${data.names.length} names for guild ${guildId} from the database`);
        }
    } catch (e) {
        logger.error("queryGuild failed:", e);
    }
}

function applyNameOverrides() {
    for (const [id, name] of nameOverrides) {
        const channel = ChannelStore.getChannel(id);
        if (!channel || channel.name === name) continue;
        try {
            (channel as any).name = name;
            FluxDispatcher.dispatch({ type: "CHANNEL_UPDATE", channel });
        } catch (e) {
            logger.error("inject failed for channel", id, e);
        }
    }
}
