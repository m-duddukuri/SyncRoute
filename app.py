"""SyncRoute: local Streamlit MVP. Run: streamlit run app.py"""
import copy
import json
import os
import re
from pathlib import Path

import streamlit as st
from optimizer import solve_trip

st.set_page_config(page_title='SyncRoute', page_icon='🔗', layout='wide')


def clock(n):
    return f'{int(n)//60:02d}:{int(n)%60:02d}'


def minutes(t):
    h,m = [int(x) for x in t.split(':')]
    return 60*h+m


def load_demo():
    return json.loads(Path(__file__).with_name('sample_trip.json').read_text())


def agent_response(message, trip, api_key):
    """LLM tool-calling is opt-in; no key uses deterministic commands."""
    if not api_key:
        lower = message.lower()
        if 'optimiz' in lower or 'plan route' in lower or 'solve' in lower:
            return 'OPTIMIZE', 'I will optimize the current trip and show the validated routes.'
        match = re.search(r'(?:finish|end|done)\s+(?:by|at)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', lower)
        if match:
            h, m, ap = int(match.group(1)), int(match.group(2) or 0), match.group(3)
            if ap:
                h = (h % 12) + (12 if ap == 'pm' else 0)
            end = h*60 + m
            if not 0 <= end <= 1440 or end <= trip['start_min']:
                return None, 'That end time must be after the trip starts.'
            trip['end_min'] = end
            return 'UPDATED', f'Trip deadline updated to {clock(end)}. Run Optimize to recalculate.'
        return None, 'I can help you plan. Try “optimize routes” or “finish by 5 pm”. Use the Trip Editor to add stops or change who attends. For natural-language trip editing, enter an OpenAI API key in the sidebar.'

    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    tools = [
        {'type':'function','function':{'name':'optimize_trip','description':'Run deterministic route optimization for the current trip','parameters':{'type':'object','properties':{},'additionalProperties':False}}},
        {'type':'function','function':{'name':'set_trip_deadline','description':'Update latest time by which all travelers must return to the start. Use minutes after midnight.','parameters':{'type':'object','properties':{'end_min':{'type':'integer','description':'Minutes after midnight, 0 to 1440'}},'required':['end_min'],'additionalProperties':False}}},
        {'type':'function','function':{'name':'update_stop','description':'Change an existing stop opening time, closing time, or visit duration, using minutes after midnight.','parameters':{'type':'object','properties':{'stop_id':{'type':'string'},'open':{'type':'integer'},'close':{'type':'integer'},'duration':{'type':'integer'}},'required':['stop_id'],'additionalProperties':False}}},
    ]
    context = {'trip_name':trip['name'],'start_min':trip['start_min'],'end_min':trip['end_min'],'people':trip['people'],'stops':trip['stops']}
    completion = client.chat.completions.create(model='gpt-4.1-mini',messages=[
        {'role':'system','content':'You are the SyncRoute planning assistant. Never invent optimized routes or travel times. Use tools for any state change or optimization. Ask clarifying questions when ambiguous. Never claim a tool was executed unless it was. Stop IDs are required to update a stop. The travel matrix is synthetic, not real-world driving time.'},
        {'role':'user','content':'Current trip data: '+json.dumps(context)+'\nUser request: '+message}],tools=tools,tool_choice='auto')
    response = completion.choices[0].message
    if not response.tool_calls:
        return None, response.content or 'Could you clarify the change you want?'
    replies, should_optimize = [], False
    for call in response.tool_calls:
        args = json.loads(call.function.arguments or '{}')
        if call.function.name == 'optimize_trip':
            should_optimize = True
            replies.append('Running the deterministic optimizer.')
        elif call.function.name == 'set_trip_deadline':
            end = args['end_min']
            if trip['start_min'] < end <= 1440:
                trip['end_min'] = end
                replies.append(f'Updated the deadline to {clock(end)}.')
            else:
                replies.append('Rejected invalid deadline.')
        elif call.function.name == 'update_stop':
            stop = next((s for s in trip['stops'] if s['id'] == args['stop_id']), None)
            if stop is None:
                replies.append('Stop ID not found; no change made.')
                continue
            new = {k: args.get(k,stop[k]) for k in ('open','close','duration')}
            if 0 <= new['open'] < new['close'] <= 1440 and 1 <= new['duration'] <= 240:
                stop.update(new)
                replies.append(f'Updated {stop["name"]}.')
            else:
                replies.append('Rejected invalid stop times or duration.')
    return 'OPTIMIZE' if should_optimize else 'UPDATED', ' '.join(replies)


if 'trip' not in st.session_state:
    st.session_state.trip = load_demo()
if 'messages' not in st.session_state:
    st.session_state.messages = []

st.title('🔗 SyncRoute')
st.caption('Independent routes. Shared arrivals. One coordinated plan.')
with st.sidebar:
    st.header('Settings')
    api_key = st.text_input('OpenAI API key (optional)', type='password', help='Only used for chat requests during this session. Never put API keys in shared source files.')
    st.caption('Without an API key, the assistant supports a few local commands. With a key, it can call controlled planning tools.')
    st.warning('Demo travel times are synthetic. Do not use these itineraries for real navigation.')
    if st.button('Reset demo trip'):
        st.session_state.trip = load_demo()
        st.session_state.messages = []
        st.session_state.pop('result', None)
        st.rerun()

trip = st.session_state.trip
editor, planner, assistant = st.tabs(['📝 Trip editor', '🗓️ Optimized itineraries', '💬 AI planning assistant'])
with editor:
    st.subheader('Trip details')
    trip['name'] = st.text_input('Trip name', trip['name'])
    c1,c2 = st.columns(2)
    trip['start_min'] = minutes(c1.text_input('Start (24-hour HH:MM)',clock(trip['start_min'])))
    trip['end_min'] = minutes(c2.text_input('Latest return (24-hour HH:MM)',clock(trip['end_min'])))
    st.write('**Travelers:** '+', '.join(p['name'] for p in trip['people']))
    st.caption('All travelers start and finish at the same synthetic depot in this first prototype. Different starting addresses are a planned next step.')
    st.subheader('Stops')
    for i, s in enumerate(trip['stops']):
        with st.expander(f'{s["name"]} · {"Shared" if len(s["people"])>1 else "Individual"}',expanded=False):
            s['name'] = st.text_input('Name',s['name'],key=f'name_{s["id"]}')
            people_names = {p['id']:p['name'] for p in trip['people']}
            selection = st.multiselect('Attendees',list(people_names),default=s['people'],format_func=lambda x:people_names[x],key=f'people_{s["id"]}')
            if selection: s['people'] = selection
            c1,c2,c3 = st.columns(3)
            s['open'] = minutes(c1.text_input('Opens',clock(s['open']),key=f'open_{s["id"]}'))
            s['close'] = minutes(c2.text_input('Closes',clock(s['close']),key=f'close_{s["id"]}'))
            s['duration'] = c3.number_input('Visit (min)',1,240,int(s['duration']),key=f'duration_{s["id"]}')
            s['required'] = st.checkbox('Required stop',s.get('required',True),key=f'required_{s["id"]}')
    st.info('Travel times are stored in the trip JSON. To add locations in this MVP, edit or import a complete trip JSON including the travel-time matrix.')
    uploaded = st.file_uploader('Import trip JSON',type=['json'])
    if uploaded and st.button('Load imported trip'):
        try:
            new_trip = json.load(uploaded)
            if len(new_trip['travel_minutes']) != len(new_trip['stops']) + 1:
                raise ValueError('Travel matrix dimensions do not match stops')
            st.session_state.trip = new_trip
            st.session_state.pop('result',None)
            st.rerun()
        except Exception as e:
            st.error(f'Invalid trip: {e}')
    st.download_button('Export trip JSON',json.dumps(trip,indent=2),file_name='syncroute_trip.json',mime='application/json')

with planner:
    st.subheader('Synchronized route optimizer')
    if st.button('⚡ Optimize routes',type='primary'):
        try:
            st.session_state.result = solve_trip(trip)
        except Exception as e:
            st.error(f'Optimization failed: {e}')
    result = st.session_state.get('result')
    if result:
        if result['status'] not in ('optimal','feasible'):
            st.error(result['message'])
        elif result['validation_errors']:
            st.error('Independent validation failed: '+', '.join(result['validation_errors']))
        else:
            st.success(f'{result["status"].title()} schedule found; all independent constraint checks passed.')
            cols = st.columns(len(result['routes']))
            for col, route in zip(cols,result['routes']):
                with col:
                    st.markdown(f'### {route["name"]}')
                    st.metric('Driving',f'{route["drive_minutes"]} min')
                    st.metric('Waiting',f'{route["wait_minutes"]} min')
                    for v in route['visits']:
                        symbol = '🔗' if v['shared'] else '📍'
                        st.markdown(f'**{symbol} {v["name"]}**  \n{clock(v["start"])}–{clock(v["end"])} · arrived {clock(v["arrival"])} · wait {v["wait"]}m')
            st.download_button('Download solution JSON',json.dumps(result,indent=2),file_name='syncroute_solution.json',mime='application/json')

with assistant:
    st.subheader('Planning assistant')
    st.caption('Try “Optimize routes”, “Finish by 5 pm”, or, with an API key, “Make Shared House One a 30-minute visit”.')
    for msg in st.session_state.messages:
        with st.chat_message(msg['role']): st.write(msg['content'])
    prompt = st.chat_input('How should we change the plan?')
    if prompt:
        st.session_state.messages.append({'role':'user','content':prompt})
        try:
            action, reply = agent_response(prompt,trip,api_key)
            if action == 'OPTIMIZE':
                result = solve_trip(trip)
                st.session_state.result = result
                reply += ' Result: '+result['status']+'. '+('Validation passed.' if not result.get('validation_errors') else 'Validation errors: '+str(result['validation_errors']))+' See Optimized itineraries tab.'
            elif action == 'UPDATED':
                st.session_state.pop('result',None)
        except Exception as e:
            reply = f'Could not complete the request: {e}'
        st.session_state.messages.append({'role':'assistant','content':reply})
        st.rerun()
