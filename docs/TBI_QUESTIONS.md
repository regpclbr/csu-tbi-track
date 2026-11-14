# Questions for the TBI

Every rule in `docs/ASSUMPTIONS.md` that is marked *pending validation*, *open item* or *placeholder*
for the TBI, with one plain question to ask. The number is the item in ASSUMPTIONS.md.
This file only lists questions: until the TBI answers, the system keeps the current rule.

The last section lists the general assumptions (items 1–14). The title of ASSUMPTIONS.md says
all of them are to be validated with the TBI too.

## Roles and permissions

- **Item 67:** Should any staff member who can create activities be able to edit *every* activity, or only the activities they created themselves?
- **Item 85:** *Decided (2026-10-31):* the System Administrator manages user accounts only and does not create or edit activities (item 339). Is that acceptable to the TBI?
- **Item 339:** Should the System Administrator be able to view activity, participant and document records for support, or only the Admin Console (stricter data privacy)?
- **Item 340:** Should account and role changes also be shown in the Admin Console as a history?
- **Items 226–227:** A staff member who forgets their password can now ask for a one-time link by email (valid for one hour), and the Admin can also email them a new temporary password. Is that acceptable to the TBI, or should only one of the two exist?

## Activities

- **Items 66, 346:** The system now moves an activity from Scheduled to Ongoing when it starts, and from Ongoing to Conducted after it ends once at least one attendance is recorded (staff can still change the status by hand). Is that right? Is it fine that registration closes when the activity starts?
- **Item 345:** Activities now have a start time and an end time (for example 8:00 AM – 5:00 PM). For activities over several days, are the hours the same every day, or does each day need its own time?
- **Items 353, 356:** An activity that has ended but is still Ongoing (no attendance recorded, or nobody registered) is listed under "Needs follow-up" until staff mark attendance or mark it as Conducted. Is that the right way to handle it, or should the system conduct it by itself after some days?

## Registration, attendance and evaluation

- **Item 105:** Which questions should the registration form and the evaluation form ask by default? (Current placeholders: registration asks for "Affiliation / Organization" and the Data Privacy consent; evaluation asks for an overall rating, a resource person rating, an organization/venue rating, key learnings and suggestions.)
- **Item 214:** How many people may register from the same campus or office network within one hour (for example a whole class during an orientation)? Public registration now allows 300 attempts per hour from one network address and 10 per email address. Are these limits enough?
- **Items 334–335:** Each activity now also has one general evaluation link (and QR code) for posters: a participant types the email they registered with and, if they attended, gets their personal evaluation link by email. One person can ask at most 3 times per hour, and 300 requests per hour are allowed from one network address. Is that acceptable?
- **Item 280:** Which email address should participants and startups write to with questions? It is shown at the bottom of every public page (registration, evaluation, set password). Placeholder for now: tbi.contact@example.com (setting `TBI_CONTACT_EMAIL`).
- **Item 111:** When attendance is "confirmed by email", what exactly happens: does the participant reply to an email and a staff member then marks them present? (There is no self check-in now.)
- **Item 111 (check-in):** Should participants check themselves in through a link (for example a QR code at the venue)? (Now only staff mark attendance.)
- **Item 3 (walk-ins):** Should walk-in participants be able to register on the day of the activity? (Now registration closes when the activity starts, and walk-ins are not recorded.)
- **Item 348:** The evaluation link is now emailed automatically once the activity is Conducted. A failed email is not retried automatically; staff can resend it. Is that acceptable?
- **Item 354:** When someone types their email on the general evaluation link, staff now see on the Evaluation tab that this registered person asked for their link and whether it was sent (or why not). Emails of people who did not register are never stored. Is that acceptable for data privacy?

## Documents

- **Item 9:** When is each required document due? (Current placeholders: Activity Design 10 days before the start date, Invitation Letter 7 days before, Request Letter 5 days before, Attendance Sheet on the end date, Accomplishment Report 7 days after the end date.)
- **Item 118 (upload):** Should any TBI staff member be allowed to upload a document for any activity, or only certain roles or the activity's organizer?
- **Item 344 (review):** Only the Director approves or returns documents now (the System Administrator can give this to another role in the Admin Console). Is that right?
- **Item 344 (stand-in):** If the Director is unavailable, who should approve documents?
- **Item 344 (own uploads):** Should the Director's own uploads need someone else's approval? (Now the Director may approve their own upload.)
- **Item 118 (fixing a document):** If a document needs fixing, is it right that only the person who uploaded it can replace or delete it, and only before it is approved? (Approved documents are final; a wrong one is fixed by uploading a new one.)
- **Item 120:** Should every TBI staff member be able to open and download every activity document, or should some documents be limited to certain roles?
- **Item 123:** Does the TBI have an official file-naming or folder convention for documents (for example "Year/Activity/Type_Date")? If yes, what is it for each document type?
- **Item 327:** Should a blank Evaluation Form document be uploadable when creating an activity? (Now it is not offered there, because the system builds the evaluation form itself; it can still be uploaded on the activity's Documents tab.)

## Reminders

- **Item 125:** Missing-document reminders go to the person who created the activity. If that person's account has been deactivated, who should get the reminders instead?

## Startups

- **Item 147:** What maturity stages does Navigatu use for startups? (Current placeholders: Ideation, Validation, Early Traction, Scaling.)
- **Item 143:** Should startups be able to request a password reset themselves, or is a link sent by the Admin enough until Navigatu SSO is available?

## AI assistant

- **Item 165:** On the free tier of the Gemini API, Google may use the content sent to it (questions and document excerpts) to improve its products. May real TBI documents be sent to it, or is a paid tier required? Today these document types are never sent: Attendance Sheet. Should any other types be excluded?
- **Item 151:** Some uploaded documents are scans or photos (no text inside). Is OCR (reading text from images) needed, so the assistant can search them too?

## Certificates

- **Items 46, 357, 361, 362:** Is this the right certificate process: the system creates the certificates by itself when the activity is Conducted (one per attendee, one per resource person); a participant's certificate is ready once they submit the evaluation and both signatures are added (a resource person's once the signatures are added); the Director, a Project Head or the System Administrator checks each ready certificate in the preview and approves it (or approves all ready ones at once); participants then get theirs by email at once and resource persons get a printed copy? An issued certificate is never changed by itself (a wrong one is replaced by a new certificate with a new number; an old signature can be updated by reissuing it, item 364).
- **Item 361 (approvers):** Should the two Project Heads and the System Administrator really approve certificates and add signatures, or only the Director? (The System Administrator can switch this per role in the Admin Console.) Is it acceptable that the System Administrator approves certificates even though they manage the user accounts?
- **Item 361 (signatures):** Signature images are added on the Signatories page or on each activity's Certificates tab (PNG or JPG). Who in the office will have the signature image files, and is it acceptable that an approver uploads them? (Since item 366 a white background is made transparent automatically, with a preview before saving, so a scan or a JPG prints cleanly.)
- **Item 365 (titles):** Staff can now print a different title under a signature for one activity only (for example "Director, CSU-TBI" instead of "Director"). Who should be allowed to change it: the staff who create activities and the certificate approvers (now), or only the approvers?
- **Item 366 (defaults):** When the default signatories change, activities that have no certificates yet and no one chosen get the new defaults automatically; a choice someone already made is never replaced. Is that right?
- **Item 357 (signatories):** The two people printed on a certificate (for example the VP or the University President, and the Director) do not approve in the system: their signature images are printed. Is that acceptable, or must they also approve each activity's certificates?
- **Item 363 (signatories per activity):** Each activity now chooses its own two signatories (left and right) from a list of people with their printed positions; new activities start with two defaults. Who should be allowed to choose them: staff who create activities and the certificate approvers (now), or only the approvers? Which two people should be the defaults?
- **Item 364 (reissue):** When a signatory or a signature image changes after certificates were issued, an approver may reissue them: the same number, a new PDF emailed again as an "Updated copy" (resource persons: printed again). Is that acceptable, or should issued certificates never be sent again?
- **Items 357, 361, 362, 363 (changes):** If the current design is applied again after approval ("Apply the current design"), a signature is replaced or an activity's signatory is changed, the certificate waits for approval again (issued ones are changed only by a reissue). Is that right?
- **Item 362 (undo attendance):** If someone was marked present by mistake after the activity was Conducted, staff may undo it while their certificate is still waiting for approval: the certificate is deleted and its number is skipped. Is a gap in the certificate numbers acceptable?
- **Item 183 (wording):** What exactly should a certificate say? (Current placeholder: "Certificate of Participation is presented to <name> for participating in "<activity>" held on <dates> at <venue>. Given on <end date> at CSU Technology Business Incubator." Resource persons: "Certificate of Appreciation … in grateful recognition of their valuable contribution as resource person on "<topic>" …".) What is the official name of the office to print?
- **Item 183 (signatories):** Which titles should appear under the two signatures (for example "Director, CSU-TBI" and "Vice President for Research, Development and Extension")? Each activity can now choose its own two people (item 363); the defaults are the Director on the left and the VP on the right. Is that order right?
- **Item 183 (date):** Should the "Given on" date be the activity's last day (now) or the day the certificate is approved or issued?
- **Item 177:** Can the TBI give us its official certificate design as an image (A4 landscape, PNG or JPG, at least 1754 × 1240 pixels), with the centre left empty for the text?
- **Item 176:** Is the number format `TBI-2026-00001` (year and a sequence that restarts every year) acceptable, or does the TBI already use another format?
- **Item 358:** Staff can now preview each certificate exactly as it will be printed, with both signatures (marked "PREVIEW" in a corner until it is approved). May every staff member see these previews, or only some roles?
- **Item 361 (new activity):** When someone other than the Director creates an activity, the Director gets an in-app notification. Should the Director also get an email (for each activity, or a daily summary)?
- **Item 175:** Participants type the name printed on their certificate and the email it is sent to when they submit the evaluation, and there is no way to correct them afterwards. Is that acceptable, or should staff be able to fix a typo before the certificate is issued?
- **Item 189:** When a resource person's certificate is handed over, does the TBI keep any proof (a signed receipt, a photo)? Should it be uploaded to the system?
- **Items 194, 362:** Should attendees who did not submit the evaluation before the deadline still receive a certificate? (Until answered: no certificate is sent; their certificate shows "Evaluation closed without an answer" and it is not a staff task.)

## Reports and dashboard

- **Item 203:** Which reports does the TBI submit, to whom (for example the university, DOST or DTI), how often (monthly, quarterly, yearly), and in what format or template? (Built now: Accomplishment, Participation, Evaluation, Document Compliance and Certificates reports, as PDF or Excel.)
- **Item 199:** Should evaluation reports include participants' written suggestions and comments (shown without names), or only the rating averages? (Now: only the rating averages.)
- **Item 195:** Is it right that a report counts only activities that were Conducted, by the date they ended? (An activity that ends on January 2 counts in the new year.)
- **Item 204:** Who should be allowed to generate reports? Now: staff whose role can create activities. Any staff member can see the dashboard and download reports. Should some roles not see them?

## General assumptions (items 1–14)

- **Item 360:** When a certificate email fails, it is retried automatically and staff see "Send failed" with a Resend button. Is that enough, or should someone be notified?
- **Item 1:** Is everyone who registers automatically accepted, with no approval step?
- **Item 2:** When all slots are taken, should registration simply close (no waiting list)?
- **Item 3:** Are walk-ins (people who did not register) never recorded as attendees?
- **Item 4:** Should evaluations be linked to the person (not anonymous), open only after the activity ends, and only for people marked present?
- **Item 5:** Should certificates be emailed automatically once they are approved and the participant has submitted the evaluation?
- **Item 6:** Do participants get a Certificate of Participation by email, and resource persons a Certificate of Appreciation in hard copy?
- **Item 7:** Is one certificate design used for all activities, managed by the Admin?
- **Item 8:** Must every uploaded document be reviewed and approved before it counts?
- **Item 9:** Are these the required documents for every activity: invitation letter, activity design, request letter, attendance sheet, accomplishment report? (Due dates: see Documents above.)
- **Item 10:** Should missing-document reminders go to the person who created the activity, inside the app (not by email)?
- **Item 11:** Is it right that startup data from Navigatu is read-only here and that startups are matched by their email address?
- **Item 12:** Can every TBI staff member see every startup?
- **Item 13:** Is it fine that the system's Admin accounts belong to the researchers?
- **Item 14:** The researchers decide the certificate numbering format. Does the TBI have an existing format it wants us to follow? (Chosen for now: `TBI-<year>-<5-digit sequence>`, see Certificates above.)
