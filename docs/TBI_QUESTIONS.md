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
- **Item 423 (evaluation without a deadline):** The evaluation deadline is now optional: left empty, the evaluation stays open and each attendee's certificate is sent as soon as they evaluate (no evaluation, no certificate). Activities that already have a deadline keep it. Is an evaluation that never closes acceptable, or should there be a longest time (for example 30 days after the activity)?
- **Item 3 (walk-ins):** Should walk-in participants be able to register on the day of the activity? (Now registration closes when the activity starts, and walk-ins are not recorded.)
- **Items 348, 419:** The evaluation link is now emailed automatically to everyone marked as attended from 3 hours before the activity ends (never before midnight of its last day; NAVIGATU UAT, 2026-11-21), and to people marked as attended later. A failed email is not retried automatically; staff can resend it, and the Evaluation tab shows when the links went out. Is 3 hours before the end the right moment?
- **Item 354:** When someone types their email on the general evaluation link, staff now see on the Evaluation tab that this registered person asked for their link and whether it was sent (or why not). Emails of people who did not register are never stored. Is that acceptable for data privacy?

## Documents

- **Item 9:** When is each required document due? *Partly answered (2026-11-21, NAVIGATU's 5-day rule, item 416):* the Activity Design and the Request Letter are both due 5 days before the start date. The Attendance Sheet is now due 5 days after the end date, like the other payment documents (item 425). Is that right?
- **Item 386 (for NAVIGATU):** The Invitation Letter and the Accomplishment Report are now optional documents: they are no longer counted as missing or overdue, they are not in the document compliance report, and no reminders are sent for them (uploaded ones are kept and listed under "Other documents"). Can NAVIGATU confirm that neither the Invitation Letter nor the Accomplishment Report is required for every activity? Should any other document type be optional too, or should some activity types still require one of them? (Still required: Activity Design, Request Letter, Attendance Sheet.) (Staff can also upload files of the new type "Other", for example an MOA, with a title of their own.)
- **Item 118 (upload):** Should any TBI staff member be allowed to upload a document for any activity, or only certain roles or the activity's organizer?
- **Item 344 (review):** Only the Director approves or returns documents now (the System Administrator can give this to another role in the Admin Console). Is that right?
- **Item 344 (stand-in):** If the Director is unavailable, who should approve documents?
- **Item 344 (own uploads):** Should the Director's own uploads need someone else's approval? (Now the Director may approve their own upload.)
- **Item 406 (for NAVIGATU, the Director's own uploads):** Every document, also one the Director uploads, waits for the Director's approval. The Director may approve their own uploads, because by default nobody else can approve documents. Is that acceptable, or should someone else (for example a Project Head) approve the Director's own documents?
- **Item 409 (for NAVIGATU; changed by item 417):** When creating an activity, staff now choose "Create as: Draft" (the default) or "Scheduled". Scheduled needs the Activity Design and the Request Letter on the form. Even when Scheduled, registration stays closed until staff create the registration form, check its fields and click "Open registration"; the Share card shows no link or QR code before that. Is that the right moment to open registration, or should someone (for example the Director) approve the form first?
- **Item 405 (for NAVIGATU; changed by item 417):** The Activity Design and the Request Letter can now be uploaded on the New activity form or later (both wait for the Director's approval). While either is missing, the creator and the Director get the usual reminders (due 5 days before the start), also for a Draft. An activity can be Scheduled once both are uploaded, even before the Director approves them. Is that right, or must they be approved before the activity is Scheduled (or before registration opens)? Should the Director also get the reminders for Scheduled activities (now only the creator does)?
- **Item 425 (payment documents, for NAVIGATU / the Supply Office):** After an activity is Conducted, the system now asks for an Activity Report, the Attendance Sheet and Event Photos (one or more JPG/PNG pictures), all due 5 days after the end date, and the Documents tab has "Download payment packet" (a zip of the approved ones with a list of what is missing). Is the **Activity Report** the same document as the **Accomplishment Report** (now optional), or a different one? Is 5 days after the end date right for all three, and does the Supply Office need any other document (for example a certificate of appearance or receipts)? (New activities only: activities that ended before November 22, 2026 do not need the Activity Report and Event Photos.)
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
- **Item 381:** When someone asks the assistant about "this week" or "next week", the system counts a week from Monday to Sunday. Is that how the TBI counts weeks? It also leaves Draft activities out of lists and counts unless the question asks for drafts. Is that right?
- **Item 383:** The assistant can list and download the files of an activity in any review state (Waiting for review, Returned, Approved), including attendance sheets, as the Documents page already does for staff. Is that acceptable? (The content of an attendance sheet is still never sent to Gemini.)

## Certificates

- **Items 46, 357, 361, 362, 369:** Is this the right certificate process: the system creates the certificates by itself when the activity is Conducted (one per attendee, one per resource person); the Director, a Project Head or the System Administrator checks them in the preview and approves them **once for the whole activity**; from then on each participant's certificate is emailed by itself as soon as they submit the evaluation, and resource persons' certificates are ready to print? An issued certificate is never changed by itself (a wrong one is replaced by a new certificate with a new number; an old signature can be updated by reissuing it, item 364).
- **Item 361 (approvers; answered by item 421):** *Answered at the UAT:* only the TBI Director approves certificates (also before the activity, item 420), reissues them and edits the default certificate text. Staff who create activities still choose the signatories, prepare the certificates, write custom text for one activity, retry a failed PDF and resend.
- **Item 421 (stand-in):** If the Director is away, who should approve certificates? (The System Administrator can switch the right on for another role in the Admin Console; until then certificates wait for the Director, who gets an in-app notification when they are waiting.)
- **Item 361 (signatures; changed by item 418):** *Answered at the UAT:* only the TBI Director adds and manages signatories and signature images (on the Signatories page or each activity's Certificates tab, PNG or JPG); approvers no longer upload them, and the System Administrator never does. Who in the office will give the Director the signature image files? Should anyone stand in for the Director (the System Administrator can switch this right on for another role)? (Since item 366 a white background is made transparent automatically, with a preview before saving, so a scan or a JPG prints cleanly.)
- **Item 420 (for NAVIGATU, prepare before the activity):** When creating an activity, staff can now tick "Prepare the certificates now (optional)": they choose the two signatories, their titles and (optionally) custom text, see a sample certificate with the name "Juan Dela Cruz", and the Director gets an in-app notification "Certificates of <activity> are ready for your review". The Director (or another certificate approver) can then approve them before the activity happens; when the activity is Conducted the certificates are created already approved, so each participant's certificate is emailed as soon as they submit the evaluation. Not ticked: nothing changes (the defaults are used and the certificates wait for approval after the activity). Is this what you meant? Should anyone besides the Director be notified (now: roles that both manage signatories and approve certificates, i.e. the Director by default)?
- **Item 420 (changes after an early approval):** After an early approval, changing a signatory, a title, the certificate text, the design, or the activity's title, dates, venue or resource persons cancels the approval, and the Director must approve again (because the printed certificate would differ from what was approved). Is that right, or should small changes (for example the venue) keep the approval?
- **Item 369 (cancelling):** After an activity's certificates are approved, changing a signatory, a title, the design or the certificate text cancels the approval, and someone must approve again. Is that right, or should small changes (for example a title) not need a new approval?
- **Item 370 (wording):** What exactly should each certificate say (title, the line before the name, the main sentence, the closing line)? Staff can now edit the wording (a default for all activities, and custom text for one activity). Who should be allowed to edit it: the certificate approvers for the default, and staff who create activities or approve certificates for one activity (now)?
- **Item 365 (titles):** Staff can now print a different title under a signature for one activity only (for example "Director, CSU-TBI" instead of "Director"). Who should be allowed to change it: the staff who create activities and the certificate approvers (now), or only the approvers?
- **Item 366 (defaults):** When the default signatories change, activities that have no certificates yet and no one chosen get the new defaults automatically; a choice someone already made is never replaced. Is that right?
- **Item 357 (signatories):** The two people printed on a certificate (for example the VP or the University President, and the Director) do not approve in the system: their signature images are printed. Is that acceptable, or must they also approve each activity's certificates?
- **Item 363 (signatories per activity; changed by item 418):** Each activity chooses its own two signatories (left and right) from a list of people with their printed positions; new activities start with two defaults. Only the Director adds people to the list, changes them and sets the defaults. Who should be allowed to choose an activity's two people from that list: staff who create activities and the certificate approvers (now), or only the Director? Which two people should be the defaults?
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

- **Item 424 (participant mix, for NAVIGATU):** The guideline says Faculty at most 30% and Incubatees 70%. **How do MSMEs count?** Until you answer, MSMEs are counted together with the Incubatees for the 70% (so an activity is within the guideline when Faculty is at most 30% and Incubatees plus MSMEs are at least 70%). Should MSMEs instead count separately (and if so, what share is allowed), or with neither group? Is the guideline checked on the attendees (now, once attendance is marked; before that on the registrations), and only for activities open to both Faculty and Incubatees or MSMEs (now)? Registration is never blocked; the activity only shows a warning.

- **Item 203:** Which reports does the TBI submit, to whom (for example the university, DOST or DTI), how often (monthly, quarterly, yearly), and in what format or template? (Built now: Accomplishment, Participation, Evaluation, Document Compliance and Certificates reports, as PDF or Excel.)
- **Item 199:** *Answered at the UAT (2026-11-17):* NAVIGATU wants the written answers and comments listed without names. They are now in each activity's evaluation summary (item 401); the period reports still show only the rating averages. Should the period Evaluation Report list them too?
- **Item 401 (interpretation scale):** Which interpretation scale do you use for evaluation ratings? (For example 4.21–5.00 Excellent … 1.00–1.80 Poor. Now the summary shows only the mean, with no interpretation.)
- **Item 399 (quarters):** Do your quarterly and semi-annual reports follow the calendar year (Q1 = January–March, first half = January–June), or another year (for example a school year)?
- **Item 402 (individual evaluations):** One participant's evaluation answers (with the name) can now be opened and downloaded as PDF by staff who can create activities (Director, both Project Heads, Admin Staff); the System Administrator and other staff see only the summary without names. Is that the right group?
- **Item 195:** Is it right that a report counts only activities that were Conducted, by the date they ended? (An activity that ends on January 2 counts in the new year.)
- **Item 384:** Staff can now type the report they want ("Participation report for this quarter, trainings only, in Excel") and the system fills in the report form for them to check. The typed request is sent to Gemini (only the request, never report data). Is that acceptable on the free tier (see item 165), and are 30 requests per staff member per hour enough?
- **Item 388 (for NAVIGATU):** Admin Staff can now create and edit activities (and therefore generate reports) and approve certificates, like the two Project Heads. They still cannot approve or return documents or manage user accounts. Can NAVIGATU confirm these rights for Admin Staff? Should Admin Staff also approve documents, or should creating activities or approving certificates be limited to some activity types?
- **Item 389 (for NAVIGATU):** The System Administrator can now delete an activity created by mistake or for testing, but never one with an issued certificate or an approved document; a record of every deletion is kept. Is that acceptable, and should anyone else be allowed to delete activities?
- **Item 390:** Members now choose a position (job title) on their profile; it is only shown under their name and gives no rights. Which positions (job titles) does the TBI use, so the System Administrator can add them?
- **Item 204:** Who should be allowed to generate reports? Now: staff whose role can create activities. Any staff member can see the dashboard and download reports. Should some roles not see them?

## Client review version and UAT data (for NAVIGATU)

- **Item 415 (no deployment):** *Decided (2026-11-21, team):* the system is not deployed anywhere. All further testing and the defense demo run only on the researchers' laptop (no cloud server, no public link). The November 17, 2026 UAT ran once on that laptop through a temporary Cloudflare link; it never ran on a cloud server such as Azure.
- **Items 391, 394 (approval for real data):** Was it acceptable to use real staff accounts and real participant data (names, emails, evaluation answers) on the researchers' laptop during the November 17 UAT, and may further testing on the laptop use real data, or only test data? Who approves this for NAVIGATU?
- **Item 394 (privacy notice text):** The public registration, startup portal registration and evaluation pages now show a short privacy notice with a required "I agree" checkbox, and a full notice at /privacy/. Can NAVIGATU (or the CSU Data Protection Officer) approve or correct the text?
- **Item 394 (contact person):** Who is the contact person (or Data Protection Officer) for privacy questions and requests, and which email address should the notice show?
- **Item 394 (retention period):** How long should participant data (registrations, attendance, evaluations, certificates) be kept after an activity, and what happens to it afterwards (deleted or anonymized)?
- **Item 414 (retention in the review version, please confirm):** *Decided by the team (2026-11-21), pending NAVIGATU:* the notice now says "In this review version, the data you enter is kept on the researchers' computer until you ask for it to be deleted, and it is not shared outside CSU and NAVIGATU" (it replaced "deleted no later than November 30, 2026"). The Data Privacy Act of 2012 (RA 10173, Sec. 11(e)) says personal data should be kept only as long as needed for its purpose, and this wording has no end date. Can NAVIGATU (or the CSU Data Protection Officer) confirm this retention, or give an end date (for example after the defense or after the study is graded) after which the review data is deleted?
- **Item 394 (two checkboxes):** *Decided (2026-11-16, owner):* the default registration form no longer has its own "Data Privacy consent" question; every public form has the fixed privacy checkbox instead. Is one checkbox linked to the full notice enough for the TBI?
- **Item 395 (accounts):** Who should get accounts on the review version, with which roles, and for how long? Should the accounts be deactivated when the review ends?
- **Item 398 (sender address):** During the review, emails come from the researchers' Gmail address. For the real deployment, which official TBI or CSU email address (and mail server) should send invitations, evaluation links and certificates?
- **Items 391, 397 (review period):** *Answered by item 415:* there is no hosted review any more; testing and the defense demo run only on the researchers' laptop. What happens to the UAT data: see item 414 above.

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
- **Item 9:** Are these the required documents for every activity: activity design, request letter, attendance sheet? (The invitation letter and the accomplishment report are optional now, item 386. Due dates: see Documents above.)
- **Item 10:** Should missing-document reminders go to the person who created the activity, inside the app (not by email)?
- **Item 11:** Is it right that startup data from Navigatu is read-only here and that startups are matched by their email address?
- **Item 12:** Can every TBI staff member see every startup?
- **Item 13:** Is it fine that the system's Admin accounts belong to the researchers?
- **Item 14:** The researchers decide the certificate numbering format. Does the TBI have an existing format it wants us to follow? (Chosen for now: `TBI-<year>-<5-digit sequence>`, see Certificates above.)
