import { Component } from 'vue';

export interface EntityForm {
  title: string,
  schema: Record<string, any>,
  data: Record<string, any>,
  action?: string,
  submitLabel?: string
    slot?: Component,
}

export interface FormElement {
  attr: string;
  label: string;
  references?: string;
  widget?: "text" | "textarea" | "key_value_map" | "reference" | "select" | "checkbox" | "custom";
  widgetComponent?: any;
  autofocus?: boolean;
  context?: string;
  options?: { value: string | null; label: string }[];
  default?: string | number | boolean | null;
  create_only?: boolean;
  tooltip?: ""[];
  help?: string;
  required?: boolean;
  readonly?: boolean;
  children?: FormElement[];
}

// Generic form elements

export const NAME: FormElement = {
  attr: "name",
  label: "Name",
};

export const FIRST_NAME: FormElement = {
  attr: "first_name",
  label: "First Name",
};

export const LAST_NAME: FormElement = {
  attr: "last_name",
  label: "Last Name",
};

export const DESCRIPTION: FormElement = {
  attr: "description",
  label: "Description",
  widget: "textarea",
};

export const ZONE: FormElement = {
  attr: "zone",
  label: "Zone",
  widget: "reference",
  references: "zone",
};

export const DIRECTION: FormElement = {
  attr: "direction",
  label: "Direction",
  options: [
    {
      value: "",
      label: "",
    },
    {
      value: "north",
      label: "North",
    },
    {
      value: "east",
      label: "East",
    },
    {
      value: "south",
      label: "South",
    },
    {
      value: "west",
      label: "West",
    },
    {
      value: "up",
      label: "Up",
    },
    {
      value: "down",
      label: "Down",
    },
  ],
};

export const CONDITIONS: FormElement = {
  attr: "conditions",
  label: "Conditions",
  help: `Conditions required for the action to be carried. For more information on conditions, refer to their <a href='https://docs.writtenrealms.com/building/conditions'>doc page</a>.<br/><br/>
    Quick reference:<br/>
    <code>
    - archetype archetype<br/>
    - core_faction faction_code<br/>
    - currency code amount<br/>
    - fact_check fact value<br/>
    - fact_above fact value<br/>
    - gender gender<br/>
    - gold gold<br/>
    - has_weapon<br/>
    - has_shield<br/>
    - health percentage<br/>
    - in_combat<br/>
    - is_mob<br/>
    - item_in_eq definition_id<br/>
    - item_in_inv definition_id<br/>
    - item_in_room definition_id<br/>
    - level level<br/>
    - marked mark value<br/>
    - mark_above mark value<br/>
    - mob_in_room definition_id<br/>
    - name<br/>
    - player_in_room<br/>
    - quest_complete quest_id<br/>
    - standing faction_code standing<br/>
    - wields_weapon_type weapon_type
    </code>`,
};

const ROOM_ACTION: FormElement[] = [
  NAME,
  {
    attr: "actions",
    label: "Action",
    help: `Command to be executed by the player to trigger the action. By using the 'or' operator, several actions may be defined`,
  },
  {
    attr: "display_action_in_room",
    label: "Display Action in Room",
    widget: "checkbox",
    default: true,
    help: `Whether to display a button for this action in the UI when looking at the room`,
  },
  {
    attr: "commands",
    label: "Commands",
    widget: "textarea",
    help: `Commands to be executed by the room when the action is triggered by the player. Can enter multiple commands, one per line.`,
  },
  CONDITIONS,
  {
    attr: "show_details_on_failure",
    label: "Show Failure Message",
    widget: "checkbox",
    default: false,
    help: `If the condition fails, whether to display a reason message for the failure. If false, the player will receive the same message they would for a command that does not exist.`,
  },
  {
    attr: "failure_message",
    label: "Failure Message",
    help: `If defined, what message to display if the action condition is not met. If 'Show Failure Message' is checked and this message is empty, the game will supply the player with its best guess as for the reason of the failure.`,
  },
  {
    attr: "gate_delay",
    label: "Action Cooldown",
    default: 10,
    help: `Applies a debounce to the action, so that a player entering the action twice in succession does not trigger the room commands with the second invocation unless the specified amount of time has elapsed.`,
  },
];

export const BUILDER_FORMS = {
  ZONE,
  NAME,
  DESCRIPTION,

  ROOM_ACTION,

  // Builder screens

  ROOM_INFO: [
    {
      ...NAME,
      autofocus: true,
    },
    {
      ...DESCRIPTION,
    },
    {
      children: [
        { attr: "x", label: "X" },
        { attr: "y", label: "Y" },
        { attr: "z", label: "Z" },
      ],
    },
    {
      children: [
        ZONE,
        {
          attr: "type",
          label: "Room Type",
          help: `Different rooms have different stamina costs for going through them:<br/>
                <br/>
                * 1 stamina: road, city, indoor<br/>
                * 2 stamina: trail, field<br/>
                * 3 stamina: forest, desert, water<br/>
                * 4 stamina: mountain<br/>
                <br/>
                In addition, different room types are colored differently in the map.`,
          options: [
            {
              value: "road",
              label: "Road",
            },
            {
              value: "trail",
              label: "Trail",
            },
            {
              value: "city",
              label: "City",
            },
            {
              value: "indoor",
              label: "Indoor",
            },
            {
              value: "field",
              label: "Field",
            },
            {
              value: "mountain",
              label: "Mountain",
            },
            {
              value: "forest",
              label: "Forest",
            },
            {
              value: "desert",
              label: "Desert",
            },
            {
              value: "water",
              label: "Water",
            },
            {
              value: "shallow",
              label: "Shallow Water",
            },
          ],
        },
      ],
    },
    {
      attr: 'color',
      label: 'Color',
      help: `Optional. If a value is specified, the room will use it as its css color.`
    },
    {
      attr: "note",
      label: "Notes",
    },
  ],

  ZONE_INFO: [
    {
      attr: "name",
      label: "Name",
    },
  ],

  ZONE_PATH_ROOM: [
    {
      attr: "room",
      label: "Room",
      references: "room",
      widget: "reference",
      context: "zone",
    },
  ],

  ZONE_PATH_DETAILS: [
    {
      attr: "name",
      label: "Name",
      widget: "text",
    },
  ],

};
