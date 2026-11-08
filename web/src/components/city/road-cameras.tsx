import { cameraFallback, useCamerasStatus } from "@/lib/camera-status";
import { CAMERA_VIEWER_URL, cameraName, readingWords, scoreWords } from "@/lib/city";
import { formatDate, formatTime } from "@/lib/report-format";
import { HazardShapeIcon } from "../ui/hazard-mark";
import Panel from "../ui/panel";
import TimeAgo from "../ui/time-ago";
import CameraGlyph from "./camera-glyph";
import { latestIncident, type CameraGroup } from "./cameras";
import styles from "./city-dashboard.module.css";

type Props = {
  cameras: readonly CameraGroup[];
  error: string | null;
  replay: boolean;
  now: Date;
  selectedId: string | null;
  onToggle: (id: string) => void;
};

/** The keyboard and screen-reader path to every camera on the map. No images, ever. */
export default function RoadCameras({ cameras, error, replay, now, selectedId, onToggle }: Props) {
  // OP-412: while the cameras are not reporting, say so and where ice risk comes from.
  const fallback = cameraFallback(useCamerasStatus(), now);
  return (
    <Panel aria-labelledby="road-cameras-title" className={styles.panel} id="road-cameras">
      <h2 id="road-cameras-title">Road cameras</h2>
      <p>
        What OpenPath&apos;s road-condition model read on each 511 Alberta camera. OpenPath never shows or keeps the
        camera pictures. 511 Alberta has no page for a single camera, so each link opens its camera list; look for
        the camera by its location there.
      </p>
      {replay && <p>These are recorded readings played back, not live ones.</p>}
      {error && (
        <p className={styles.problem}>
          <strong>Camera readings could not be loaded.</strong> {error} Refresh the list to try again.
        </p>
      )}
      {!error && fallback && (
        <p className={styles.empty} data-camera-state={fallback.state}>
          <strong>{fallback.sentence}</strong>
        </p>
      )}
      {!error && cameras.length === 0 && (
        <p className={styles.empty}>
          {!fallback && "No camera readings from the last two hours. "}
          The camera worker reads each camera every few minutes while it runs; when it is off, the ice-risk weather
          watch still applies.
        </p>
      )}
      {cameras.length > 0 && (
        <ul className={styles.cameras}>
          {cameras.map((camera) => {
            const name = cameraName({ camera_id: camera.cameraId, view_id: camera.views[0].properties.view_id });
            const pressed = selectedId === camera.id;
            const several = camera.views.length > 1;
            // OP-415: display only. The caution shape plus the words, never colour alone.
            const incident = latestIncident(camera);
            return (
              <li key={camera.id} className={pressed ? styles.cameraSelected : undefined}>
                <h3 className={styles.cameraName} id={`${camera.id}-name`.replace(":", "-")}>
                  <CameraGlyph size={20} className={styles.cameraIcon} />
                  {name}
                </h3>
                <ul className={styles.views}>
                  {camera.views.map((view, index) => {
                    const reading = view.properties;
                    const score = scoreWords(reading.score);
                    return (
                      <li key={view.id}>
                        {several && <>View {index + 1}: </>}
                        <strong>{readingWords(reading)}</strong>
                        {score && <>. {score}</>}. <TimeAgo iso={reading.observed_at} now={now} />.
                      </li>
                    );
                  })}
                </ul>
                {incident && (
                  <p className={styles.incident} data-camera-incident="seen">
                    <HazardShapeIcon shape="caution" size={20} className={styles.incidentIcon} />
                    <span>
                      Possible vehicle incident in view at{" "}
                      <time dateTime={incident.observed_at} title={formatDate(incident.observed_at)}>
                        {formatTime(incident.observed_at)}
                      </time>
                      , unverified.
                    </span>
                  </p>
                )}
                <p className={styles.meta}>{camera.headline.attribution}.</p>
                <div className={styles.cameraActions}>
                  <button
                    type="button"
                    className="button button-text"
                    aria-pressed={pressed}
                    aria-describedby={`${camera.id}-name`.replace(":", "-")}
                    onClick={() => onToggle(camera.id)}
                  >
                    Show on map
                  </button>
                  {pressed && (
                    <a className={`${styles.external} ${styles.mapHint}`} href="#city-map">
                      See it on the map
                    </a>
                  )}
                  <a className={styles.external} href={CAMERA_VIEWER_URL} rel="noreferrer">
                    Open 511 Alberta&apos;s camera list<span className="sr-only"> to find {name}</span>
                  </a>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
