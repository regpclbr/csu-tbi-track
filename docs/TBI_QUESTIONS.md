# Questions for the TBI

Every rule in `docs/ASSUMPTIONS.md` that is marked *pending validation*, *open item* or *placeholder*
for the TBI, with one plain question to ask. The number is the item in ASSUMPTIONS.md.
This file only lists questions: until the TBI answers, the system keeps the current rule.

The last section lists the general assumptions (items 1–14). The title of ASSUMPTIONS.md says
all of them are to be validated with the TBI too.

## Roles and permissions

- **Item 67:** Should any staff member who can create activities be able to edit *every* activity, or only the activities they created themselves?
- **Item 85:** Should the Admin role (the system administrator) also be able to create and edit activities, or only manage user accounts?
- **Item 61:** When a staff member forgets their password, should the Admin be able to reset it for them from the system?

## Activities

- **Item 66:** Is it fine that staff change an activity's status by hand (Draft → Scheduled → Ongoing → Conducted), instead of the system changing it automatically by date?

## Registration, attendance and evaluation

- **Item 105:** Which questions should the registration form and the evaluation form ask by default? (Current placeholders: registration asks for "Affiliation / Organization" and the Data Privacy consent; evaluation asks for an overall rating, a resource person rating, an organization/venue rating, key learnings and suggestions.)
- **Item 111:** When attendance is "confirmed by email", what exactly happens: does the participant reply to an email and a staff member then marks them present? (There is no self check-in now.)

## Documents

- **Item 9:** When is each required document due? (Current placeholders: Activity Design 10 days before the start date, Invitation Letter 7 days before, Request Letter 5 days before, Attendance Sheet on the end date, Accomplishment Report 7 days after the end date.)
- **Item 118 (upload):** Should any TBI staff member be allowed to upload a document for any activity, or only certain roles or the activity's organizer?
- **Item 118 (review):** Who should approve or return a document? Now it is any staff member whose role can create activities, but never the person who uploaded it.
- **Item 118 (fixing a document):** If a document needs fixing, is it right that only the person who uploaded it can replace or delete it, and only before it is approved? (Approved documents are final; a wrong one is fixed by uploading a new one.)
- **Item 120:** Should every TBI staff member be able to open and download every activity document, or should some documents be limited to certain roles?
- **Item 123:** Does the TBI have an official file-naming or folder convention for documents (for example "Year/Activity/Type_Date")? If yes, what is it for each document type?

## Reminders

- **Item 125:** Missing-document reminders go to the person who created the activity. If that person's account has been deactivated, who should get the reminders instead?

## Certificates

- **Item 46:** Is this the right certificate process: Generated → Pending Sign-off → Approved → Issued, one step at a time, with a rejected certificate going back from Pending Sign-off to Generated, and an issued certificate never changed (a wrong one is replaced by a new certificate with a new number)?

## General assumptions (items 1–14)

- **Item 1:** Is everyone who registers automatically accepted, with no approval step?
- **Item 2:** When all slots are taken, should registration simply close (no waiting list)?
- **Item 3:** Are walk-ins (people who did not register) never recorded as attendees?
- **Item 4:** Should evaluations be linked to the person (not anonymous), open only after the activity ends, and only for people marked present?
- **Item 5:** Should certificates be emailed automatically once a certificate is approved and the participant has submitted the evaluation?
- **Item 6:** Do participants get a Certificate of Participation by email, and resource persons a Certificate of Appreciation in hard copy?
- **Item 7:** Is one certificate design used for all activities, managed by the Admin?
- **Item 8:** Must every uploaded document be reviewed and approved before it counts?
- **Item 9:** Are these the required documents for every activity: invitation letter, activity design, request letter, attendance sheet, accomplishment report? (Due dates: see Documents above.)
- **Item 10:** Should missing-document reminders go to the person who created the activity, inside the app (not by email)?
- **Item 11:** Is it right that startup data from Navigatu is read-only here and that startups are matched by their email address?
- **Item 12:** Can every TBI staff member see every startup?
- **Item 13:** Is it fine that the system's Admin accounts belong to the researchers?
- **Item 14:** The researchers decide the certificate numbering format. Does the TBI have an existing format it wants us to follow?
