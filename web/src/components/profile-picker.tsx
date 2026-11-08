import { TravelProfile, travelProfiles } from "@/lib/planner-types";

type Props = { value: TravelProfile; onChange: (profile: TravelProfile) => void };

// Native radio buttons in a fieldset: arrow keys move between profiles.
export default function ProfilePicker({ value, onChange }: Props) {
  return (
    <fieldset className="profile-fieldset">
      <legend>
        <span className="eyebrow">2. Your travel needs</span>
        <strong>How are you moving?</strong>
      </legend>
      <div className="profile-grid">
        {travelProfiles.map((option) => (
          <label className={`profile-option${value === option.id ? " selected" : ""}`} key={option.id}>
            <input
              type="radio"
              name="travel-profile"
              value={option.id}
              checked={value === option.id}
              onChange={() => onChange(option.id)}
            />
            <span className="profile-icon" aria-hidden="true">{option.icon}</span>
            <span>
              <strong>{option.label}</strong>
              <small>{option.description}</small>
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
